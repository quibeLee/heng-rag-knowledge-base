from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.llm.prompts import REFUSAL_ANSWER
from app.retrieval.vector_retriever import VectorRetriever
from app.workflows.rag_state import RAGState
async def retrieve(state: RAGState, session: AsyncSession) -> RAGState:
    retriever = VectorRetriever(session)
    chunks = await retriever.search(state["query"], top_k=settings.retrieval_top_k)
    # 检索为空 / 最高相似度过低 → 直接拒答；不再调 LLM
    refused = not chunks or chunks[0].score < settings.retrieval_min_score
    update: RAGState = {
        "retrieved_chunks": chunks,
        "refused": refused,
    }
    if refused:
        update["answer"] = REFUSAL_ANSWER
    return update