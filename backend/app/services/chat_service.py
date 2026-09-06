from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import AnswerCitation, Conversation, Message
from app.db.repositories.citation_repo import AnswerCitationRepository
from app.db.repositories.conversation_repo import ConversationRepository
from app.db.session import AsyncSessionLocal
from app.retrieval.vector_retriever import RetrievedChunk
from app.workflows.graph import get_rag_graph
from app.workflows.nodes import load_context, stream_generate
from app.workflows.rag_state import RAGState
from app.llm.answer_verifier import VerifyResult, get_answer_verifier
from app.llm.prompts import REFUSAL_ANSWER

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

    async def list_conversations(
            self, page: int, page_size: int
    ) -> tuple[list[tuple[Conversation, int]], int]:
        repo = ConversationRepository(self.session)
        return await repo.list_page(page=page, page_size=page_size)

    async def delete_conversation(self, conversation_id: UUID) -> None:
        repo = ConversationRepository(self.session)
        deleted = await repo.delete(conversation_id)
        if not deleted:
            raise NotFoundError("会话不存在")
        await self.session.commit()

    async def stream_answer(
            self, conversation_id: UUID, question: str
    ) -> AsyncIterator[dict]:
        """逐事件 yield SSE 载荷。

        事件协议（与前端约定）：
            message_start → query_route → agent_steps → citations → token...
            → [verify_result] → message_end
        - 拒答路径不发 verify_result（拒答本身已经是终态）
        - verify_result.verified=False 时携带 replacement_answer，前端按它整段
          替换流式出来的答案，与 PRD"校验失败 → 拒答替换"对齐
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

                # 1. 加载上下文（仅历史消息，本轮 user 此刻尚未入库）。
                # load_context 是唯一需要 DB session 的节点，由 service 先填好再交给图。
                state.update(await load_context(state, session))

                # 2. 跑 RAG 子图：normalize_query → route_query → 检索决策循环
                final_state = await get_rag_graph().ainvoke(state)
                state.update(final_state)  # type: ignore[arg-type]
                # 3. user 消息落库
                await self._persist_user_message(state, session)

                yield {
                    "event": "message_start",
                    "data": {"user_message_id": str(state["user_message_id"])},
                }

                # 4. 把 query 路由结果推给前端调试面板（始终发送，前端按 route 选择渲染）
                yield {
                    "event": "query_route",
                    "data": _build_query_route_payload(state),
                }
                yield {
                    "event": "agent_steps",
                    "data": {"steps": _serialize_agent_steps(state)},
                }

                # 5. retrieve（含拒答判定）→ 先把引用发给前端，让参考资料面板立刻可见
                # 拒答路径下不下发 citations，retrieved_chunks 可能还残留 agent 循环
                # 中途某一轮的候选（被 observe_context 判为不足），但语义上既然已经拒答，
                # 前端就不该再展示这些参考资料
                citations_payload = (
                    []
                    if state.get("refused")
                    else [
                        _serialize_citation(c, ordinal=i)
                        for i, c in enumerate(
                            state.get("retrieved_chunks", []), start=1
                        )
                    ]
                )
                yield {
                    "event": "citations",
                    "data": {"citations": citations_payload},
                }

                # 6. 生成：拒答直接走拒答文案；否则逐 token 流式
                verify_result: VerifyResult | None = None
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

                    # 6. 答案校验，verify 失败替换为拒答文案
                    if settings.verify_answer_enabled:
                        verify_result = await get_answer_verifier().verify(
                            question=state["query"],
                            answer=state["answer"],
                            chunks=list(state.get("retrieved_chunks", [])),
                        )
                        replacement = (
                            REFUSAL_ANSWER if not verify_result.verified else None
                        )
                        if not verify_result.verified:
                            # 严格按 PRD：替换成统一拒答文案 + 标 refused，
                            # 前端按 replacement_answer 覆盖正文 + 清空引用
                            state["answer"] = REFUSAL_ANSWER
                            state["refused"] = True
                        yield {
                            "event": "verify_result",
                            "data": _build_verify_payload(
                                verify_result, replacement_answer=replacement
                            ),
                        }

                # 7. assistant 消息 + citations 同事务落库（保证两者原子）
                await self._persist_assistant_message(
                    state, session, verify_result=verify_result
                )

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
        """流式开始前先把 user 消息落库并 commit；首次提问时顺手把会话标题改成问题前 30 字。

        必须在 load_context 之后调用：load_context 读到的"历史消息"不应包含本轮提问。
        标题更新放在同一事务里，避免新建会话后侧栏一直显示「新对话」。
        """
        repo = ConversationRepository(session)
        # 首次提问（会话还没有任何消息）→ 把默认标题改成本次问题
        if await repo.count_messages(state["conversation_id"]) == 0:
            await repo.update_title_if_default(
                state["conversation_id"], state["question"]
            )
        user_msg = ConversationRepository.make_user_message(
            state["conversation_id"], content=state["question"]
        )
        await repo.add_messages([user_msg])
        await session.commit()
        state["user_message_id"] = user_msg.id

    async def _persist_assistant_message(
            self,
            state: RAGState,
            session: AsyncSession,
            *,
            verify_result: VerifyResult | None,
    ) -> None:
        """流式生成结束后落库 assistant 消息及其引用，单事务保证两者原子。"""
        conv_repo = ConversationRepository(session)
        citation_repo = AnswerCitationRepository(session)

        extra_metadata: dict = {
            "refused": bool(state.get("refused")),
            "query_route": _build_query_route_payload(state),
            "agent_steps": _serialize_agent_steps(state),
        }
        if verify_result is not None:
            # verify_result 复用 SSE 载荷格式，但 metadata 不需要 replacement_answer
            extra_metadata["verify_result"] = _build_verify_payload(
                verify_result, replacement_answer=None
            )

        assistant_msg = ConversationRepository.make_assistant_message(
            state["conversation_id"],
            content=state["answer"],
            # 把 query 路由结果持久化到 metadata 字段，刷新历史时前端调试面板还能继续展示
            extra_metadata=extra_metadata,
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


def _serialize_agent_steps(state: RAGState) -> list[dict]:
    """SSE / metadata 共用的 agent_steps 载荷格式。
    state 内字段全部用原生 Python 类型，直接 JSON 序列化即可；这里做一层显式拷贝
    避免后续节点继续追加时影响已发出的事件 / 已持久化的 metadata。
    """
    return [dict(step) for step in state.get("agent_steps", [])]


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
        "rerank_score": (
            round(chunk.rerank_score, 4) if chunk.rerank_score is not None else None
        )
    }


def _build_verify_payload(
        result: VerifyResult, *, replacement_answer: str | None
) -> dict:
    """verify_result SSE / metadata 共用载荷。
    replacement_answer 仅在 verified=False 时携带；前端按它整段替换流式出来的答案，
    与 PRD"verify 失败 → 拒答替换"语义对齐。
    """
    payload: dict = {
        "verified": result.verified,
        "reason": result.reason or None,
    }
    if not result.verified and replacement_answer is not None:
        payload["replacement_answer"] = replacement_answer
    return payload
