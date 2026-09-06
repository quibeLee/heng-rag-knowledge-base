from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import AnswerCitation, Conversation, Message
from app.db.repositories.citation_repo import AnswerCitationRepository
from app.db.repositories.conversation_repo import ConversationRepository
from app.db.session import AsyncSessionLocal
from app.retrieval.vector_retriever import RetrievedChunk
from app.workflows.nodes import (
    load_context,
    normalize_query,
    retrieve,
    route_query,
    stream_generate,
)
from app.workflows.rag_state import RAGState

logger = get_logger(__name__)

def _build_query_route_payload(state: RAGState) -> dict:
    """SSE / metadata 共用的 query_route 载荷格式。
    始终携带 4 个可选字段（None 也保留），前端可据此判断展示哪种调试面板。
    """
    return {
        "route": state.get("route", "original"),
        "query": state.get("query", ""),
        "rewritten_query": state.get("rewritten_query"),
        "hyde_answer": state.get("hyde_answer"),
        "multi_queries": state.get("multi_queries"),
    }

def _serialize_citation(chunk: RetrievedChunk, ordinal: int) -> dict:
    """citations SSE 事件载荷格式（与前端约定一致）。
    ordinal 必须显式传入：与 prompt 中给 LLM 看到的「片段 N」编号一致，
    前端按这个数字渲染 [N] 角标，避免后续顺序丢失导致引用串号。
    """
    return {
        "ordinal": ordinal,
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "document_name": chunk.document_name,
        "page_no": chunk.page_no,
        "section_path": chunk.section_path,
        "score": round(chunk.score, 4),
        "quote": chunk.content,
        "retrieval_meta": _build_retrieval_meta(chunk),
    }


class ChatService:
    """注意：
    - 非流式接口（创建会话 / 历史）使用 FastAPI 注入的请求级 session
    - 流式问答使用独立 session（与请求生命周期解耦），由 stream_answer 内部管理
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_conversation(self, title: str = "新对话") -> Conversation:
        repo = ConversationRepository(self.session)
        conversation = await repo.create(title=title)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_conversation(self, conversation_id: UUID) -> Conversation:
        repo = ConversationRepository(self.session)
        conversation = await repo.get(conversation_id)
        if conversation is None:
            raise NotFoundError("会话不存在")
        return conversation

    async def list_messages(
            self, conversation_id: UUID
    ) -> tuple[Conversation, list[Message]]:
        # 先校验会话存在，避免"空会话"和"会话不存在"被混淆
        conversation = await self.get_conversation(conversation_id)
        repo = ConversationRepository(self.session)
        messages = await repo.list_messages(conversation_id)
        return conversation, messages

    async def stream_answer(
            self, conversation_id: UUID, question: str
    ) -> AsyncIterator[dict]:
        """逐事件 yield SSE 载荷。
        事件协议（与前端约定）：
            message_start → citations → token... → message_end
        任何阶段出错则改 yield error 并提前结束。
        """
        # 校验会话存在用 service 自带 session；流式跑用独立 session
        await self.get_conversation(conversation_id)
        async with AsyncSessionLocal() as session:
            try:
                state: RAGState = {
                    "conversation_id": conversation_id,
                    "question": question,
                }

                # 1. 加载上下文（仅历史消息，本轮 user 此刻尚未入库）+ 改写查询 + 路由查询
                state.update(await load_context(state, session))
                state.update(await normalize_query(state))
                state.update(await route_query(state))
                # 2. user 消息落库
                await self._persist_user_message(state, session)

                yield {
                    "event": "message_start",
                    "data": {"user_message_id": str(state["user_message_id"])},
                }

                # 3. 把 query 路由结果推给前端调试面板（始终发送，前端按 route 选择渲染）
                yield {
                    "event": "query_route",
                    "data": _build_query_route_payload(state),
                }

                # 4. retrieve（含拒答判定）→ 先把引用发给前端，让参考资料面板立刻可见
                state.update(await retrieve(state))

                citations_payload = [
                    _serialize_citation(c, ordinal=i)
                    for i, c in enumerate(state.get("retrieved_chunks", []), start=1)
                ]
                yield {
                    "event": "citations",
                    "data": {"citations": citations_payload},
                }

                # 5. 生成：拒答直接走拒答文案；否则逐 token 流式
                if state.get("refused"):
                    yield {
                        "event": "token",
                        "data": {"delta": state["answer"]},
                    }
                else:
                    answer_parts: list[str] = []
                    async for delta in stream_generate(state):
                        answer_parts.append(delta)
                        yield {"event": "token", "data": {"delta": delta}}
                    state["answer"] = "".join(answer_parts)

                # 6. assistant 消息 + citations 同事务落库（保证两者原子）
                await self._persist_assistant_message(state, session)

                yield {
                    "event": "message_end",
                    "data": {
                        "message_id": str(state["assistant_message_id"]),
                        "refused": bool(state.get("refused")),
                    },
                }

            except Exception as exc:
                logger.exception("chat stream failed: conversation_id=%s", conversation_id)
                # 注意：user 消息可能已在前面独立 commit，这里只回滚未提交的部分
                # （比如 assistant 写入中途失败）。保留 user 消息便于前端排查 / 重试。
                await session.rollback()
                yield {
                    "event": "error",
                    "data": {
                        "code": "chat_stream_failed",
                        "message": str(exc).strip() or "问答处理失败",
                    },
                }

    async def _persist_user_message(
            self, state: RAGState, session: AsyncSession
    ) -> None:
        """流式开始前先把 user 消息落库并 commit。

        必须在 load_context 之后调用：load_context 读到的"历史消息"不应包含本轮提问。
        """
        repo = ConversationRepository(session)
        user_msg = ConversationRepository.make_user_message(
            state["conversation_id"], content=state["question"]
        )
        await repo.add_messages([user_msg])
        await session.commit()
        state["user_message_id"] = user_msg.id

    async def _persist_assistant_message(
            self, state: RAGState, session: AsyncSession
    ) -> None:
        """流式生成结束后落库 assistant 消息及其引用，单事务保证两者原子。"""
        conv_repo = ConversationRepository(session)
        citation_repo = AnswerCitationRepository(session)

        assistant_msg = ConversationRepository.make_assistant_message(
            state["conversation_id"],
            content=state["answer"],
            # 把 query 路由结果持久化到 metadata 字段，刷新历史时前端调试面板还能继续展示
            extra_metadata={
                "refused": bool(state.get("refused")),
                "query_route": _build_query_route_payload(state),
            },
        )
        await conv_repo.add_messages([assistant_msg])

        if not state.get("refused"):
            citations = [
                AnswerCitation(
                    message_id=assistant_msg.id,
                    ordinal=ordinal,
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    document_name=chunk.document_name,
                    page_no=chunk.page_no,
                    quote=chunk.content,
                    retrieval_meta=_build_retrieval_meta(chunk),
                )
                for ordinal, chunk in enumerate(
                    state.get("retrieved_chunks", []), start=1
                )
            ]
            await citation_repo.bulk_add(citations)

        await session.commit()
        state["assistant_message_id"] = assistant_msg.id

def _build_retrieval_meta(chunk: RetrievedChunk) -> dict:
    """混合检索调试元数据"""
    return {
        "sources": list(chunk.sources),
        "vector_rank": chunk.vector_rank,
        "vector_score": (
            round(chunk.vector_score, 4) if chunk.vector_score is not None else None
        ),
        "keyword_rank": chunk.keyword_rank,
        "keyword_score": (
            round(chunk.keyword_score, 4) if chunk.keyword_score is not None else None
        ),
        "rrf_score": (
            round(chunk.rrf_score, 6) if chunk.rrf_score is not None else None
        ),
    }