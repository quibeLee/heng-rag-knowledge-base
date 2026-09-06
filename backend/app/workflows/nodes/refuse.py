from app.llm.prompts import REFUSAL_ANSWER
from app.workflows.rag_state import RAGState
async def refuse(state: RAGState) -> RAGState:
    return {"refused": True, "answer": REFUSAL_ANSWER}