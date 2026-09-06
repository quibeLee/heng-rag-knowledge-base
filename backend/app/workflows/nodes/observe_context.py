from app.core.config import settings
from app.retrieval.vector_retriever import RetrievedChunk
from app.workflows.rag_state import RAGState
async def observe_context(state: RAGState) -> RAGState:
    chunks: list[RetrievedChunk] = state.get("retrieved_chunks", [])
    sufficient = _is_sufficient(chunks)
    top_score = (
        round(chunks[0].vector_score, 4)
        if chunks and chunks[0].vector_score is not None
        else None
    )
    steps = list(state.get("agent_steps", []))
    if steps:
        last = dict(steps[-1])
        last["retrieved_count"] = len(chunks)
        last["top_score"] = top_score
        last["sufficient"] = sufficient
        steps[-1] = last
    return {
        "agent_steps": steps,
        "retrieval_round": state.get("retrieval_round", 0) + 1,
        "context_sufficient": sufficient,
    }
def _is_sufficient(chunks: list[RetrievedChunk]) -> bool:
    """与 retrieve._should_refuse 互补：Top1 语义相似度过阈值即视为足够。"""
    if not chunks:
        return False
    top = chunks[0]
    if top.vector_score is None:
        return False
    return top.vector_score >= settings.retrieval_min_score