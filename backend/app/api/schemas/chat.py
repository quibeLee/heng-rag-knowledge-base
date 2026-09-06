from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

MessageRoleValue = Literal["user", "assistant", "system"]
QueryRouteValue = Literal["original", "rewrite", "hyde", "multi_query"]

AgentActionValue = Literal[
    "initial", "proceed", "rewrite_query", "switch_route", "refuse"
]
class AgentStep(BaseModel):
    """Agentic RAG 单轮决策 + 观察快照。
    plan_retrieval 先填决策字段（round / action / reason / route / query），
    retrieve 跑完后 observe_context 回填观察字段（retrieved_count / top_score / sufficient）。
    """
    round: int
    action: AgentActionValue
    reason: str
    route: QueryRouteValue
    query: str
    retrieved_count: int | None = None
    top_score: float | None = None
    sufficient: bool | None = None

class QueryRouteRead(BaseModel):
    """Query 优化的调试快照。仅 assistant 消息会带，前端用于渲染调试面板。"""

    route: QueryRouteValue
    query: str
    rewritten_query: str | None = None
    hyde_answer: str | None = None
    multi_queries: list[str] | None = None


class ConversationCreate(BaseModel):
    title: str = Field("新对话", min_length=1, max_length=256)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class RetrievalMeta(BaseModel):
    """混合检索调试元数据。
    - sources：该 chunk 命中的检索路（vector / keyword），两路都命中即"混合"
    - *_rank：在该路召回结果中的名次（从 1 开始），用于复盘排序
    - vector_score：cosine similarity，绝对值有意义，做拒答阈值用
    - keyword_score：ts_rank，相对值，跨 query 不可比
    - rrf_score：两路融合分，仅在同一次检索内可比
    """
    sources: list[str] = Field(default_factory=list)
    vector_rank: int | None = None
    vector_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    rrf_score: float | None = None


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
    # 混合检索调试元数据；
    retrieval_meta: RetrievalMeta | None = None

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
            retrieval_meta=_parse_retrieval_meta(citation.retrieval_meta),
        )


def _parse_retrieval_meta(raw: dict | None) -> RetrievalMeta | None:
    """历史消息没有 retrieval_meta，非法/缺失静默返回 None。"""
    if not isinstance(raw, dict):
        return None
    try:
        return RetrievalMeta.model_validate(raw)
    except Exception:
        return None


class MessageRead(BaseModel):
    id: UUID
    role: MessageRoleValue
    content: str
    created_at: datetime
    citations: list[CitationRead] = Field(default_factory=list)
    # assistant 消息的 query 路由调试信息；user / 旧消息为 None
    query_route: QueryRouteRead | None = None
    # Agentic RAG 决策轨迹；user / 旧消息 / 关闭 agent loop 时为 None
    agent_steps: list[AgentStep] | None = None

    @classmethod
    def from_orm(cls, message) -> "MessageRead":  # type: ignore[no-untyped-def]
        is_assistant = message.role == "assistant"
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            citations=[CitationRead.from_orm(c) for c in message.citations]
            if is_assistant
            else [],
            query_route=_parse_query_route(message.extra_metadata)
            if is_assistant
            else None,
            agent_steps=_parse_agent_steps(message.extra_metadata)
            if is_assistant
            else None,
        )

def _parse_agent_steps(metadata: dict | None) -> list[AgentStep] | None:
    """从 messages.extra_metadata 解析 agent_steps；缺失 / 非法静默返回 None。"""
    if not metadata:
        return None
    raw = metadata.get("agent_steps")
    if not isinstance(raw, list) or not raw:
        return None
    parsed: list[AgentStep] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            parsed.append(AgentStep.model_validate(item))
        except Exception:
            return None
    return parsed

def _parse_query_route(metadata: dict | None) -> QueryRouteRead | None:
    """从 messages.metadata 中提取 query_route 字段。

    历史消息没有这个字段，非法/缺失时静默返回 None，不阻断接口。
    """
    if not metadata:
        return None
    raw = metadata.get("query_route")
    if not isinstance(raw, dict):
        return None
    try:
        return QueryRouteRead.model_validate(raw)
    except Exception:
        return None


class ConversationDetail(BaseModel):
    """会话详情：会话本身 + 历史消息（含引用）。"""
    conversation: ConversationRead
    messages: list[MessageRead]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
