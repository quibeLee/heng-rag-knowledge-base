import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.observability import get_current_trace_id, build_trace_url
from app.db.models import AnswerCitation, Conversation, Message, User
from app.db.repositories.citation_repo import AnswerCitationRepository
from app.db.repositories.conversation_repo import ConversationRepository
from app.db.session import AsyncSessionLocal
from app.ingestion import embedder
from app.retrieval.vector_retriever import RetrievedChunk
from app.services.permission_service import (
    WILDCARD_PERMISSION_TAG,
    compute_user_permission_tags
)
from app.services.semantic_cache_service import get_semantic_cache, CachedAnswer
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


def _safe_uuid(raw: str | None) -> UUID | None:
    """缓存里 citation 的 document_id / chunk_id 是字符串，落库需要 UUID。
    历史快照中可能为 None（原 chunk 已被删），按可空字段处理。
    """
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EvaluationAnswer:
    """评测专用：跑一遍 RAG 拿到的非流式结果快照。
    与 stream_answer 不同：不写 conversations / messages，避免评测污染线上历史。
    chunks 直接给原始 RetrievedChunk，便于上层算 RAGAS retrieved_contexts。
    """
    answer: str
    refused: bool
    chunks: list[RetrievedChunk]
    query_route: dict
    agent_steps: list[dict]
    verify_result: VerifyResult | None
    trace_id: str | None
    latency_ms: int
    # 首 token 延迟（毫秒）：从 stream_answer 开始到 LLM 吐出第一个 token 为止
    # 拒答路径 / 报错时为 None
    first_token_latency_ms: int | None
    error_message: str | None = None
    citations: list[dict] = field(default_factory=list)

@dataclass(frozen=True)
class MCPChatAnswer:
    """MCP `ask_knowledge_base` 工具的非流式问答结果。
    与评测路径不同的是：MCP 调用方是真实用户（持 JWT），需要按其权限标签
    过滤检索结果；与 stream_answer 不同的是：不创建 conversation、不写
    user / assistant 消息，外部 Agent 自己管多轮上下文。
    """
    answer: str
    refused: bool
    citations: list[dict]
    trace_id: str | None

class ChatService:
    """注意：
    - 非流式接口（创建会话 / 历史）使用 FastAPI 注入的请求级 session
    - 流式问答使用独立 session（与请求生命周期解耦），由 stream_answer 内部管理
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_conversation(self, *, user_id: UUID, title: str = "新对话") -> Conversation:
        repo = ConversationRepository(self.session)
        conversation = await repo.create(title=title, user_id=user_id)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_conversation(self, conversation_id: UUID, *, user_id: UUID | None) -> Conversation:
        repo = ConversationRepository(self.session)
        conversation = await repo.get(conversation_id, user_id=user_id)
        if conversation is None:
            raise NotFoundError("会话不存在")
        return conversation

    async def list_messages(
            self, conversation_id: UUID, *, user_id: UUID | None
    ) -> tuple[Conversation, list[Message]]:
        # 先校验会话存在，避免"空会话"和"会话不存在"被混淆
        conversation = await self.get_conversation(conversation_id, user_id=user_id)
        repo = ConversationRepository(self.session)
        messages = await repo.list_messages(conversation_id)
        return conversation, messages

    async def list_conversations(
            self, page: int, page_size: int, *, user_id: UUID | None
    ) -> tuple[list[tuple[Conversation, int]], int]:
        repo = ConversationRepository(self.session)
        return await repo.list_page(page=page, page_size=page_size, user_id=user_id)

    async def delete_conversation(self, conversation_id: UUID, *, user_id: UUID | None) -> None:
        repo = ConversationRepository(self.session)
        deleted = await repo.delete(conversation_id, user_id=user_id)
        if not deleted:
            raise NotFoundError("会话不存在")
        await self.session.commit()

    @traceable(name="ChatService.stream_answer", run_type="chain")
    async def stream_answer(
            self, conversation_id: UUID, question: str, *, current_user: User
    ) -> AsyncIterator[dict]:
        """逐事件 yield SSE 载荷。事件协议（与前端约定）：
            message_start → query_route → agent_steps → citations → token...
            → [verify_result] → message_end
        - 拒答路径不发 verify_result（拒答本身已经是终态）
        - verify_result.verified=False 时携带 replacement_answer，前端按它整段
          替换流式出来的答案，与 PRD"校验失败 → 拒答替换"对齐
        任何阶段出错则改 yield error 并提前结束。
        """
        # 校验会话存在用 service 自带 session；流式跑用独立 session
        await self.get_conversation(conversation_id, user_id=current_user.id)
        permissions = compute_user_permission_tags(current_user)
        async with AsyncSessionLocal() as session:
            try:
                # 取 LangSmith trace_id：@traceable 已经为本次 stream_answer 建好 root run，
                # 这里读到的就是整次问答的 trace_id；未启用观测时返回 None
                trace_id = get_current_trace_id()
                state: RAGState = {
                    "conversation_id": conversation_id,
                    "question": question,
                    "permissions": permissions,
                    "trace_id": trace_id,
                }

                # 1. 加载上下文（仅历史消息，本轮 user 此刻尚未入库）。
                # load_context 是唯一需要 DB session 的节点，由 service 先填好再交给图。
                state.update(await load_context(state, session))

                # 2. 语义缓存：相似度阈值 + 权限范围一致才视为命中。
                # 命中时跳过整个图与 LLM，直接发缓存答案；query_embedding 在
                # 未命中分支由 retrieve 节点重新算（这里不复用是为了让缓存查询
                # 与主链路解耦，便于学员单点关闭）
                cache_hit, query_embedding = await self._try_cache_lookup(
                    question, permissions
                )
                if cache_hit is not None:
                    async for event in self._stream_cache_hit(
                            state, session, cache_hit, trace_id
                    ):
                        yield event
                    return
                # 3. 跑 RAG 子图：normalize_query → route_query → 检索决策循环
                final_state = await get_rag_graph().ainvoke(state)
                state.update(final_state)  # type: ignore[arg-type]
                # 4. user 消息落库 + 首次提问自动改会话标题
                await self._persist_user_message(state, session)

                yield {
                    "event": "message_start",
                    "data": {
                        "user_message_id": str(state["user_message_id"]),
                        "trace_id": trace_id,
                        "trace_url": build_trace_url(trace_id),
                    },
                }

                # 把 query 路由结果推给前端调试面板（始终发送，前端按 route 选择渲染）
                yield {
                    "event": "query_route",
                    "data": _build_query_route_payload(state),
                }
                yield {
                    "event": "agent_steps",
                    "data": {"steps": _serialize_agent_steps(state)},
                }

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

                # 5. 生成：拒答直接走拒答文案；否则逐 token 流式
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

                # 8. 写回缓存：仅非拒答 + verify 通过路径，写入「问题向量 + 答案 + 引用」
                # query_embedding 在 cache lookup 阶段已经算过一次，直接复用
                if (
                        settings.semantic_cache_enabled
                        and not state.get("refused")
                        and query_embedding is not None
                ):
                    await get_semantic_cache().save(
                        question=question,
                        query_embedding=query_embedding,
                        answer=state["answer"],
                        citations=citations_payload,
                        permission_scope=permissions,
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
            # LangSmith trace_id 落库，刷新历史时前端仍可展示 / 跳转
            "trace_id": state.get("trace_id"),
            # 正常RAG路径的消息一定不是缓存命中
            "cache_hit": False,
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

    async def _persist_cached_assistant_message(
            self,
            state: RAGState,
            session: AsyncSession,
            *,
            citations: list[dict],
    ) -> None:
        """缓存命中路径下落库 assistant 消息。

        - `cache_hit=True` 写入 metadata，刷新历史时前端继续展示「缓存命中」Tag
        - citations 完全复用缓存里的快照（含 retrieval_meta），保证历史与命中
          原始问答完全一致；新引用 row 由后端基于 ordinal 顺序重建
        """
        conv_repo = ConversationRepository(session)
        citation_repo = AnswerCitationRepository(session)

        assistant_msg = ConversationRepository.make_assistant_message(
            state["conversation_id"],
            content=state["answer"],
            extra_metadata={
                "refused": False,
                "trace_id": state.get("trace_id"),
                "cache_hit": True,
            },
        )
        await conv_repo.add_messages([assistant_msg])

        citation_rows = [
            AnswerCitation(
                message_id=assistant_msg.id,
                ordinal=int(c.get("ordinal") or idx + 1),
                document_id=_safe_uuid(c.get("document_id")),
                chunk_id=_safe_uuid(c.get("chunk_id")),
                document_name=c.get("document_name", ""),
                page_no=c.get("page_no"),
                quote=c.get("quote", ""),
                retrieval_meta=c.get("retrieval_meta"),
            )
            for idx, c in enumerate(citations)
        ]
        if citation_rows:
            await citation_repo.bulk_add(citation_rows)

        await session.commit()
        state["assistant_message_id"] = assistant_msg.id

    async def _try_cache_lookup(
            self, question: str, permissions: list[str]
    ) -> tuple[CachedAnswer | None, list[float] | None]:
        """查询语义缓存。

        返回 (命中项, query_embedding)：
        - 关掉缓存或 embedding 失败时全部返回 (None, None)，主链路兜底
        - 未命中时返回 (None, embedding)，外层在 save 阶段可以直接复用
        """
        if not settings.semantic_cache_enabled:
            return None, None
        try:
            embedding = await embedder.get_embeddings().aembed_query(question)
        except Exception:
            logger.exception("semantic cache: embedding failed, skip lookup")
            return None, None
        cached = await get_semantic_cache().lookup(embedding, permissions)
        return cached, embedding

    async def _stream_cache_hit(
            self,
            state: RAGState,
            session: AsyncSession,
            cached: CachedAnswer,
            trace_id: str | None,
    ) -> AsyncIterator[dict]:
        """命中分支的简化 SSE：跳过 query_route / agent_steps / verify_result，
        但仍要落库 user / assistant 消息，保证历史记录完整。"""
        state["answer"] = cached.answer
        state["refused"] = False

        await self._persist_user_message(state, session)
        yield {
            "event": "message_start",
            "data": {
                "user_message_id": str(state["user_message_id"]),
                "trace_id": trace_id,
                "trace_url": build_trace_url(trace_id),
                "cache_hit": True,
            },
        }
        yield {
            "event": "citations",
            "data": {"citations": cached.citations},
        }
        # 命中路径不流式：缓存答案瞬时可用，一次性整段发给前端，前端按 token 累加渲染
        yield {"event": "token", "data": {"delta": cached.answer}}

        await self._persist_cached_assistant_message(
            state, session, citations=cached.citations
        )
        yield {
            "event": "message_end",
            "data": {
                "message_id": str(state["assistant_message_id"]),
                "refused": False,
            },
        }

    # --- 评测专用 ---
    @traceable(name="ChatService.answer_for_evaluation", run_type="chain")
    async def answer_for_evaluation(self, question: str) -> EvaluationAnswer:
        """跑一遍完整 RAG 拿非流式结果，用于离线评测。
        与 stream_answer 区别：
        - 不创建 conversation，不落 user/assistant 消息（评测不污染线上历史）
        - chat_history 强制空：评测集每条独立， contextualize 改写自动跳过
        - 把流式 token 聚合成完整 answer 后再做 verify_answer 校验
        - 失败时把 error_message 落到 EvaluationAnswer，由调用方决定怎么记录
        """
        started_at = time.perf_counter()
        trace_id = get_current_trace_id()
        state: RAGState = {
            "conversation_id": UUID(int=0),  # 占位，评测不写库所以用不到
            "question": question,
            "chat_history": [],
            "permissions": [WILDCARD_PERMISSION_TAG],
            "trace_id": trace_id,
        }
        try:
            final_state = await get_rag_graph().ainvoke(state)
            state.update(final_state)  # type: ignore[arg-type]
            verify_result: VerifyResult | None = None
            first_token_latency_ms: int | None = None
            if state.get("refused"):
                answer = state["answer"]
            else:
                # stream_generate 是 AsyncIterator[str]，评测里直接拼成整段；
                # 首个 token yield 时记录耗时，作为「首 token 延迟」指标
                parts: list[str] = []
                async for delta in stream_generate(state):
                    if first_token_latency_ms is None:
                        first_token_latency_ms = int(
                            (time.perf_counter() - started_at) * 1000
                        )
                    parts.append(delta)
                answer = "".join(parts)
                state["answer"] = answer
                if settings.verify_answer_enabled:
                    verify_result = await get_answer_verifier().verify(
                        question=question,
                        answer=answer,
                        chunks=list(state.get("retrieved_chunks", [])),
                    )
                    if not verify_result.verified:
                        # 与 stream_answer 同口径：校验失败覆盖成统一拒答
                        answer = REFUSAL_ANSWER
                        state["answer"] = answer
                        state["refused"] = True
            chunks = list(state.get("retrieved_chunks", []))
            refused = bool(state.get("refused"))
            citations = (
                []
                if refused
                else [_serialize_citation(c, ordinal=i) for i, c in enumerate(chunks, 1)]
            )
            return EvaluationAnswer(
                answer=answer,
                refused=refused,
                chunks=chunks,
                query_route=_build_query_route_payload(state),
                agent_steps=_serialize_agent_steps(state),
                verify_result=verify_result,
                trace_id=trace_id,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                first_token_latency_ms=first_token_latency_ms,
                citations=citations,
            )
        except Exception as exc:
            logger.exception("evaluation answer failed: question=%r", question)
            return EvaluationAnswer(
                answer="",
                refused=False,
                chunks=[],
                query_route=_build_query_route_payload(state),
                agent_steps=_serialize_agent_steps(state),
                verify_result=None,
                trace_id=trace_id,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                first_token_latency_ms=None,
                error_message=str(exc).strip() or exc.__class__.__name__,
            )
    # --- MCP 专用 ---
    @traceable(name="ChatService.answer_for_mcp", run_type="chain")
    async def answer_for_mcp(
            self,
            question: str,
            *,
            current_user: User,
    ) -> MCPChatAnswer:
        """MCP `ask_knowledge_base` 工具入口：跑一次完整 RAG 拿非流式结果。
        与 stream_answer 的差异：
        - 不创建 conversation、不写 user / assistant 消息（MCP 调用不污染会话历史）
        - chat_history 强制空：外部 Agent 自管多轮，contextualize 改写自动跳过
        - 把流式 token 聚合成完整 answer 后再做 verify_answer 校验
        - 异常**直接 raise**，由 tool 层翻译成 ToolError 给 Agent
        与 answer_for_evaluation 的差异：
        - 真实用户身份：按 `permission_tags` 过滤检索；评测一律用 ["*"] 通配
        - 不记录延迟指标 / error_message：调用方失败时直接抛出更直观
        """
        permissions = compute_user_permission_tags(current_user)
        trace_id = get_current_trace_id()
        state: RAGState = {
            "conversation_id": UUID(int=0),  # 占位，MCP 不写库
            "question": question,
            "chat_history": [],
            "permissions": permissions,
            "trace_id": trace_id,
        }
        final_state = await get_rag_graph().ainvoke(state)
        state.update(final_state)  # type: ignore[arg-type]
        if state.get("refused"):
            answer = state["answer"]
        else:
            parts: list[str] = []
            async for delta in stream_generate(state):
                parts.append(delta)
            answer = "".join(parts)
            state["answer"] = answer
            if settings.verify_answer_enabled:
                verify_result = await get_answer_verifier().verify(
                    question=question,
                    answer=answer,
                    chunks=list(state.get("retrieved_chunks", [])),
                )
                if not verify_result.verified:
                    answer = REFUSAL_ANSWER
                    state["answer"] = answer
                    state["refused"] = True
        refused = bool(state.get("refused"))
        citations = (
            []
            if refused
            else [
                _serialize_citation(c, ordinal=i)
                for i, c in enumerate(state.get("retrieved_chunks", []), start=1)
            ]
        )
        return MCPChatAnswer(
            answer=answer,
            refused=refused,
            citations=citations,
            trace_id=trace_id,
        )