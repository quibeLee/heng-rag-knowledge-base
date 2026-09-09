"""关键词检索器：query → tsquery → ts_rank Top-K。
接口与 VectorRetriever 对齐（都暴露 `search(query, top_k)`），
便于 HybridRetriever 把两路当成对称输入做 RRF 融合。
适用场景：制度名 / 接口名 / 产品型号 / 编号 / 专有名词等需要精确匹配的查询，
向量检索在这些场景下经常被同义近邻干扰。
"""
from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repositories.chunk_repo import DocumentChunkRepository
from app.retrieval.vector_retriever import RetrievedChunk


class KeywordRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self.chunk_repo = DocumentChunkRepository(session)

    @traceable(name="KeywordRetriever.search", run_type="retriever")
    async def search(self, query: str, top_k: int, *, permission_tags: list[str] | None = None) -> list[RetrievedChunk]:
        rows = await self.chunk_repo.keyword_search(query, top_k, permission_tags=permission_tags)
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_name=chunk.document.name,
                content=chunk.content,
                page_no=chunk.page_no,
                section_path=chunk.section_path,
                # ts_rank 没有上界，但在同一查询内的相对大小可比，作为单路 score 直接透出
                score=ts_rank,
                sources=("keyword",),
                keyword_rank=rank,
                keyword_score=ts_rank,
            )
            for rank, (chunk, ts_rank) in enumerate(rows, start=1)
        ]
