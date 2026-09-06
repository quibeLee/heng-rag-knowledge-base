from collections.abc import Sequence
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.models import Conversation, Message, MessageRole


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, title: str = "新对话") -> Conversation:
        conversation = Conversation(title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return await self.session.get(Conversation, conversation_id)

    async def list_messages(self, conversation_id: UUID) -> list[Message]:
        """按时间正序返回所有消息（含引用）。前端展示历史用。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .options(selectinload(Message.citations))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def recent_messages(self, conversation_id: UUID, limit: int) -> list[Message]:
        """取最近 N 条消息，按时间正序返回。"""
        if limit <= 0:
            return []
        # 先按倒序取 N 条，再在 Python 侧反转为正序
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return list(reversed(rows))

    async def add_messages(self, messages: Sequence[Message]) -> None:
        if not messages:
            return
        self.session.add_all(messages)
        await self.session.flush()

    @staticmethod
    def make_user_message(conversation_id: UUID, content: str) -> Message:
        return Message(conversation_id=conversation_id, role=MessageRole.USER, content=content)

    @staticmethod
    def make_assistant_message(
            conversation_id: UUID,
            content: str,
            *,
            extra_metadata: dict | None = None,
    ) -> Message:
        return Message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=content,
            extra_metadata=extra_metadata or {},
        )
