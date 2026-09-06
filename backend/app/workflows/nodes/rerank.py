from app.core.config import settings
from app.llm.reranker import get_reranker
from app.workflows.rag_state import RAGState
async def rerank(state: RAGState) -> RAGState:
    chunks = state.get("retrieved_chunks", [])
    if not settings.rerank_enabled or len(chunks) <= 1:
        return {}
    reranked = await get_reranker().rerank(state["query"], chunks)
    return {"retrieved_chunks": reranked[: settings.retrieval_top_k]}