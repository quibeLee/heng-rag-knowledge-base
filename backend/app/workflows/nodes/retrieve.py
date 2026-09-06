"""retrieve：执行向量 Top-K 检索，并判断是否触发拒答。
multi_query 路径下需要多路召回 + 去重；其他路径走单路。
"""
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.llm.prompts import REFUSAL_ANSWER
from app.retrieval.vector_retriever import RetrievedChunk, VectorRetriever
from app.workflows.rag_state import RAGState


async def retrieve(state: RAGState, session: AsyncSession) -> RAGState:
    retriever = VectorRetriever(session)
    top_k = settings.retrieval_top_k
    if state.get("route") == "multi_query" and state.get("multi_queries"):
        # 各子查询独立召回，再合并；不在这里做 RRF，留给下一期
        bundles: list[list[RetrievedChunk]] = []
        for sub_query in state["multi_queries"] or []:
            bundles.append(await retriever.search(sub_query, top_k=top_k))
        chunks = _merge_chunks(bundles, top_k=top_k)
    else:
        chunks = await retriever.search(state["query"], top_k=top_k)
    refused = not chunks or chunks[0].score < settings.retrieval_min_score
    update: RAGState = {
        "retrieved_chunks": chunks,
        "refused": refused,
    }
    if refused:
        update["answer"] = REFUSAL_ANSWER
    return update


def _merge_chunks(
        bundles: list[list[RetrievedChunk]], top_k: int
) -> list[RetrievedChunk]:
    """多路召回结果去重 + 取 Top-K。
    同一个 chunk 可能在多条子查询中都命中；这里保留最高 score，
    再整体按 score 降序取前 top_k。
    """
    best: dict[str, RetrievedChunk] = {}
    for bundle in bundles:
        for chunk in bundle:
            key = str(chunk.chunk_id)
            prev = best.get(key)
            if prev is None or chunk.score > prev.score:
                best[key] = chunk
    ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)
    return ranked[:top_k]
