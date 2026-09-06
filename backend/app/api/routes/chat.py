from collections.abc import AsyncIterable
from uuid import UUID
from fastapi import APIRouter
from fastapi.responses import EventSourceResponse
from fastapi.sse import ServerSentEvent
from app.api.deps import DbSession
from app.api.schemas.chat import (
    ChatRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
)
from app.services.chat_service import ChatService
router = APIRouter(prefix="/conversations", tags=["chat"])
@router.post(
    "",
    response_model=ConversationRead,
    status_code=201,
    operation_id="createConversation",
)
async def create_conversation(
    payload: ConversationCreate,
    session: DbSession,
) -> ConversationRead:
    service = ChatService(session)
    conversation = await service.create_conversation(title=payload.title)
    return ConversationRead.model_validate(conversation)
@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="getConversation",
)
async def get_conversation(
    conversation_id: UUID,
    session: DbSession,
) -> ConversationDetail:
    """返回会话本身 + 全部历史消息（含引用）。"""
    service = ChatService(session)
    conversation, messages = await service.list_messages(conversation_id)
    return ConversationDetail(
        conversation=ConversationRead.model_validate(conversation),
        messages=[MessageRead.from_orm(m) for m in messages],
    )
@router.post(
    "/{conversation_id}/chat",
    operation_id="streamChat",
    response_class=EventSourceResponse,
)
async def stream_chat(
    conversation_id: UUID,
    payload: ChatRequest,
    session: DbSession,
) -> AsyncIterable[ServerSentEvent]:
    """SSE 流式问答。
    事件协议：message_start → citations → token...(多次) → message_end；
    任何阶段出错改 yield error。前端用 @microsoft/fetch-event-source 接。
    """
    service = ChatService(session)
    async for sse_event in service.stream_answer(conversation_id, payload.question):
        yield ServerSentEvent(
            data=sse_event["data"],
            event=sse_event["event"],
        )