"""混合检索器：向量 + 中文全文双路召回 + RRF 融合。
为什么用 RRF 而不是直接相加两路分数：
- 两路分数量纲完全不同（cosine sim ∈ [0,1] vs ts_rank ∈ [0,+∞)），加起来没有物理意义
- RRF 只看排名，不依赖分数尺度，是工业界混合检索事实标准
- 公式：score(d) = sum_i 1 / (k + rank_i(d))，rank 从 1 开始
- 常数 k（默认 60）越小越偏向高排名条目；越大越平滑
实现选择：在应用层用 dict 累加，而不是写一条 FULL OUTER JOIN 大 SQL，
"""
import asyncio
from uuid import UUID

from langsmith import traceable

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.vector_retriever import RetrievedChunk, VectorRetriever

logger = get_logger(__name__)


class HybridRetriever:
    """两路独立 session 并发召回。
    刻意不接收外部 session：
    - SQLAlchemy AsyncSession 不支持并发执行，两路 gather 共用一个 session 会
      把底层 asyncpg 连接搞成 InFailedSQLTransactionError，污染调用方事务
    - 检索过程纯只读，与调用方的写事务（落库 user / assistant 消息）天然解耦
    """

    @traceable(name="HybridRetriever.search", run_type="retriever")
    async def search(
            self,
            query: str,
            *,
            recall_top_k: int,
            final_top_k: int,
            permission_tags: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """两路并发召回 + RRF 融合 + 取 final Top-K。
        任一路异常都退化为另一路结果，避免一处抖动阻断整个问答。
        """
        vector_hits, keyword_hits = await asyncio.gather(
            self._safe_search(VectorRetriever, query, recall_top_k, "vector", permission_tags=permission_tags),
            self._safe_search(KeywordRetriever, query, recall_top_k, "keyword", permission_tags=permission_tags),
        )
        return rrf_fuse(
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
            k=settings.rrf_k,
            top_k=final_top_k,
        )

    @staticmethod
    async def _safe_search(
            retriever_cls: type[VectorRetriever] | type[KeywordRetriever],
            query: str,
            top_k: int,
            label: str,
            permission_tags: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        try:
            async with AsyncSessionLocal() as session:
                retriever = retriever_cls(session)
                return await retriever.search(query, top_k, permission_tags=permission_tags)
        except Exception:
            logger.exception("hybrid retrieve %s 路异常，降级为空结果", label)
            return []


def rrf_fuse(
        vector_hits: list[RetrievedChunk],
        keyword_hits: list[RetrievedChunk],
        *,
        k: int,
        top_k: int,
) -> list[RetrievedChunk]:
    """RRF 融合：rank 从 1 开始，分数 = sum 1/(k + rank)。
    保留两路的 rank / score 与命中来源，便于前端调试面板展示。
    """
    by_id: dict[UUID, RetrievedChunk] = {}
    for rank, hit in enumerate(vector_hits, start=1):
        by_id[hit.chunk_id] = _with_vector(hit, rank=rank, k=k)
    for rank, hit in enumerate(keyword_hits, start=1):
        existing = by_id.get(hit.chunk_id)
        if existing is None:
            by_id[hit.chunk_id] = _with_keyword(hit, rank=rank, k=k)
        else:
            # 同 chunk 跨两路命中：合并 sources / 累加 RRF 分数
            by_id[hit.chunk_id] = _merge_keyword_into(existing, hit, rank=rank, k=k)
    fused = sorted(
        by_id.values(),
        key=lambda c: c.rrf_score or 0.0,
        reverse=True,
    )
    return fused[:top_k]


def _with_vector(hit: RetrievedChunk, *, rank: int, k: int) -> RetrievedChunk:
    rrf_score = 1.0 / (k + rank)
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        document_name=hit.document_name,
        content=hit.content,
        page_no=hit.page_no,
        section_path=hit.section_path,
        score=rrf_score,
        sources=("vector",),
        vector_rank=rank,
        vector_score=hit.vector_score,
        rrf_score=rrf_score,
    )


def _with_keyword(hit: RetrievedChunk, *, rank: int, k: int) -> RetrievedChunk:
    rrf_score = 1.0 / (k + rank)
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        document_name=hit.document_name,
        content=hit.content,
        page_no=hit.page_no,
        section_path=hit.section_path,
        score=rrf_score,
        sources=("keyword",),
        keyword_rank=rank,
        keyword_score=hit.keyword_score,
        rrf_score=rrf_score,
    )


def _merge_keyword_into(
        existing: RetrievedChunk,
        keyword_hit: RetrievedChunk,
        *,
        rank: int,
        k: int,
) -> RetrievedChunk:
    """existing 是已经只带 vector 信息的条目；把 keyword 路的 rank/score 叠加上来。"""
    new_rrf = (existing.rrf_score or 0.0) + 1.0 / (k + rank)
    return RetrievedChunk(
        chunk_id=existing.chunk_id,
        document_id=existing.document_id,
        document_name=existing.document_name,
        content=existing.content,
        page_no=existing.page_no,
        section_path=existing.section_path,
        score=new_rrf,
        sources=("vector", "keyword"),
        vector_rank=existing.vector_rank,
        vector_score=existing.vector_score,
        keyword_rank=rank,
        keyword_score=keyword_hit.keyword_score,
        rrf_score=new_rrf,
    )
