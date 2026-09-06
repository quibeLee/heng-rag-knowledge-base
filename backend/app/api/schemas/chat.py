from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

MessageRoleValue = Literal["user", "assistant", "system"]


class ConversationCreate(BaseModel):
    title: str = Field("新对话", min_length=1, max_length=256)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class CitationRead(BaseModel):
    """assistant 消息引用的 chunk 快照。
    document_id / chunk_id 可能为空（原文档 / chunk 已被删除）。
    """
    id: UUID
    # 与 prompt 中的「片段 N」编号一致；前端渲染 [N] 角标用，避免按数组下标渲染
    # 在历史接口里被 UUID 排序乱序后串号
    ordinal: int
    document_id: UUID | None = None
    chunk_id: UUID | None = None
    document_name: str
    page_no: int | None = None
    quote: str

    @classmethod
    def from_orm(cls, citation) -> "CitationRead":  # type: ignore[no-untyped-def]
        return cls(
            id=citation.id,
            ordinal=citation.ordinal,
            document_id=citation.document_id,
            chunk_id=citation.chunk_id,
            document_name=citation.document_name,
            page_no=citation.page_no,
            quote=citation.quote,
        )


class MessageRead(BaseModel):
    id: UUID
    role: MessageRoleValue
    content: str
    created_at: datetime
    citations: list[CitationRead] = Field(default_factory=list)

    @classmethod
    def from_orm(cls, message) -> "MessageRead":  # type: ignore[no-untyped-def]
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            citations=[CitationRead.from_orm(c) for c in message.citations]
            if message.role == "assistant"
            else [],
        )


class ConversationDetail(BaseModel):
    """会话详情：会话本身 + 历史消息（含引用）。"""
    conversation: ConversationRead
    messages: list[MessageRead]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
