from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.repositories.conversation_repo import ConversationRepository
from app.workflows.rag_state import RAGState
async def load_context(state: RAGState, session: AsyncSession) -> RAGState:
    repo = ConversationRepository(session)
    # 多轮窗口按消息条数截取
    history = await repo.recent_messages(
        state["conversation_id"], limit=settings.chat_history_window * 2
    )
    return {"chat_history": history}