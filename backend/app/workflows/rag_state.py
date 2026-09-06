from typing import TypedDict, Literal
from uuid import UUID
from app.db.models import Message
from app.retrieval.vector_retriever import RetrievedChunk

QueryRoute = Literal["original", "rewrite", "hyde", "multi_query"]


class RAGState(TypedDict, total=False):
    # 输入
    conversation_id: UUID
    question: str
    # load_context 产出
    chat_history: list[Message]
    # normalize_query 产出（本章 = question）
    query: str
    # route_query 产出
    # route：实际采用的策略；query：覆盖 normalize_query 的透传 query（rewrite/hyde 路径下变成改写文本）
    route: QueryRoute
    rewritten_query: str | None
    hyde_answer: str | None
    multi_queries: list[str] | None
    # retrieve 产出
    retrieved_chunks: list[RetrievedChunk]
    # 是否触发拒答（检索不足）。True 时跳过 generate
    refused: bool
    # Agentic RAG 循环：plan_retrieval / observe_context 产出
    # agent_steps 每一项形如 {round, action, reason, route, query, retrieved_count, top_score, sufficient}
    # 由 plan_retrieval 追加"决策"字段、observe_context 回填"观察"字段，避免分两条记录
    agent_steps: list[dict]
    retrieval_round: int
    # observe_context 判定本轮候选是否足够；True 时图走向 END
    context_sufficient: bool
    # generate 产出
    answer: str
    # chat_service 落库后回写
    user_message_id: UUID
    assistant_message_id: UUID
