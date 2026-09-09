from dataclasses import dataclass, field
from uuid import UUID

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repositories.chunk_repo import DocumentChunkRepository
from app.ingestion.embedder import get_embeddings


@dataclass(frozen=True)
class RetrievedChunk:
    """检索结果中单个 chunk 的展示视图。
        score 是统一后的"越大越相似"分数：
     向量路：cosine similarity ∈ [0, 1]
     关键词路：ts_rank（无固定上界，相对比较有意义）
     混合路：RRF 融合分（参考 rrf_score 字段）

    sources / vector_rank / keyword_rank / rrf_score 是调试字段，
    用于让前端面板看清楚"这条引用从哪条路召回、各自第几名"。
    单路检索时只有该路的 rank 有值；混合检索后字段会同时填上。
    """
    chunk_id: UUID
    document_id: UUID
    document_name: str
    content: str
    page_no: int | None
    section_path: str | None
    score: float
    sources: tuple[str, ...] = field(default_factory=tuple)
    vector_rank: int | None = None
    vector_score: float | None = None  # 原始 cosine similarity（向量路命中时填充）
    keyword_rank: int | None = None
    keyword_score: float | None = None  # 原始 ts_rank（关键词路命中时填充）
    rrf_score: float | None = None
    # reranker query-chunk 成对打分的相关度，越大越相关
    # qwen3-rerank 输出 relevance_score ∈ [0, 1]
    rerank_score: float | None = None


class VectorRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self.chunk_repo = DocumentChunkRepository(session)

    @traceable(name="VectorRetriever.search", run_type="retriever")
    async def search(self, query: str, top_k: int, *, permission_tags: list[str] | None = None) -> list[RetrievedChunk]:
        # 单 query 走 aembed_query，DashScope 单条调用更直接
        embedding = await get_embeddings().aembed_query(query)
        rows = await self.chunk_repo.vector_search(embedding, top_k, permission_tags=permission_tags)
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_name=chunk.document.name,
                content=chunk.content,
                page_no=chunk.page_no,
                section_path=chunk.section_path,
                # pgvector cosine_distance ∈ [0, 2]；标准化为 similarity ∈ [-1, 1]
                # 同方向归一化向量下，distance ∈ [0, 1]，similarity ∈ [0, 1]
                score=1.0 - distance,
                sources=("vector",),
                vector_rank=rank,
                vector_score=1.0 - distance,
            )
            for rank, (chunk, distance) in enumerate(rows, start=1)
        ]
