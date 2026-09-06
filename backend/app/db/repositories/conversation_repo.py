from collections.abc import Sequence
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.models import Conversation, Message, MessageRole

from sqlalchemy import func

DEFAULT_CONVERSATION_TITLE = "新对话"


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, title: str = DEFAULT_CONVERSATION_TITLE) -> Conversation:
        conversation = Conversation(title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def count_messages(self, conversation_id: UUID) -> int:
        stmt = select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return await self.session.get(Conversation, conversation_id)

    async def delete(self, conversation_id: UUID) -> bool:
        """硬删会话；messages / answer_citations 由 ON DELETE CASCADE 自动清理。
        返回是否真正删了一行，不存在时返回 False，便于路由 404 兜底。
        """
        conversation = await self.get(conversation_id)
        if conversation is None:
            return False
        await self.session.delete(conversation)
        await self.session.flush()
        return True

    async def update_title_if_default(
            self, conversation_id: UUID, title: str
    ) -> None:
        """首次提问后把"新对话"自动改成问题前 N 字。
        只在当前 title 仍是默认值时改，避免覆盖用户手动改过的标题。
        """
        new_title = title.strip()
        if not new_title:
            return
        conversation = await self.get(conversation_id)
        if conversation is None or conversation.title != DEFAULT_CONVERSATION_TITLE:
            return
        conversation.title = new_title[:30]
        await self.session.flush()

    async def list_page(
            self, page: int, page_size: int
    ) -> tuple[list[tuple[Conversation, int]], int]:
        """按 updated_at 倒序分页，返回 (会话, 消息数) 列表 + 总数。
        消息数用一次 LEFT JOIN + GROUP BY 拿，避免 N+1 查询。
        """
        page = max(page, 1)
        page_size = max(min(page_size, 100), 1)
        offset = (page - 1) * page_size
        msg_count = func.count(Message.id).label("message_count")
        stmt = (
            select(Conversation, msg_count)
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).all()
        items = [(row[0], int(row[1])) for row in rows]
        total = int(
            (await self.session.execute(select(func.count(Conversation.id)))).scalar_one()
        )
        return items, total

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
