from dataclasses import dataclass
from typing import get_args

from app.core.logging import get_logger
from app.db.models import Message, MessageRole
from app.llm.models import get_chat_model
from app.llm.prompts import (
    build_contextualize_messages,
    build_hyde_messages,
    build_multi_query_messages,
    build_rewrite_messages,
    build_route_messages,
)
from app.workflows.rag_state import QueryRoute

logger = get_logger(__name__)
_VALID_ROUTES: tuple[str, ...] = get_args(QueryRoute)


@dataclass(frozen=True)
class QueryRouteResult:
    """Query 优化的统一返回结构。
    - route：最终采用的策略
    - query：要喂给向量检索的查询文本（rewrite/hyde 路径下是改写文本，其余是原问题）
    - 余下三个字段按 route 选择性填充，便于前端调试展示和落库
    """
    route: QueryRoute
    query: str
    rewritten_query: str | None = None
    hyde_answer: str | None = None
    multi_queries: list[str] | None = None


class QueryRewriter:
    """4 步 Query 优化的协同入口。所有 LLM 调用都走单例 ChatOpenAI。"""

    async def decide_route(self, question: str) -> QueryRoute:
        messages = build_route_messages(question)
        response = await get_chat_model().ainvoke(messages)
        raw = _extract_text(response.content).strip().lower()
        # 模型偶尔会把策略名包在引号 / 句号里，这里做一次容错切词
        token = raw.strip("\"'`.。 ")
        if token in _VALID_ROUTES:
            return token  # type: ignore[return-value]
        logger.warning("query route 模型返回非法值，降级 original：raw=%r", raw)
        return "original"

    async def rewrite(self, question: str) -> str:
        messages = build_rewrite_messages(question)
        response = await get_chat_model().ainvoke(messages)
        return _extract_text(response.content).strip()

    async def hyde(self, question: str) -> str:
        messages = build_hyde_messages(question)
        response = await get_chat_model().ainvoke(messages)
        return _extract_text(response.content).strip()

    async def multi_query(self, question: str, n: int) -> list[str]:
        messages = build_multi_query_messages(question, n)
        response = await get_chat_model().ainvoke(messages)
        text = _extract_text(response.content)
        queries = [line.strip(" -•*0123456789.、") for line in text.splitlines()]
        return [q for q in queries if q][:n]

    async def apply_route(self, question: str, route: QueryRoute, multi_query_count: int) -> QueryRouteResult:
        """已知目标 route 时，按 route 执行对应改写链路并填充 QueryRouteResult。
        与 optimize 的区别：optimize 内部先调 decide_route 判定 route，再调本方法分发；
        apply_route 跳过判定环节，直接按调用方给定的 route 执行——第 7 章 Agentic RAG
        在 switch_route 决策时由 planner 直接指定 route，复用这套分发逻辑补齐字段，
        避免在 plan_retrieval 节点里再写一遍 if-elif 与降级兜底。
        """
        try:
            if route == "rewrite":
                rewritten = await self.rewrite(question)
                # rewrite 返回空也降级，避免后续用空字符串去 embedding
                if not rewritten:
                    return QueryRouteResult(route="original", query=question)
                return QueryRouteResult(
                    route="rewrite", query=rewritten, rewritten_query=rewritten
                )
            if route == "hyde":
                hyde_answer = await self.hyde(question)
                if not hyde_answer:
                    return QueryRouteResult(route="original", query=question)
                # HyDE 用假设答案做检索；hyde_answer 字段额外保留同一份文本，供前端调试面板展示和落库审计
                return QueryRouteResult(
                    route="hyde", query=hyde_answer, hyde_answer=hyde_answer
                )
            if route == "multi_query":
                queries = await self.multi_query(question, multi_query_count)
                # 至少要有 2 条子查询才有意义，否则没必要多路召回
                if len(queries) < 2:
                    return QueryRouteResult(route="original", query=question)
                return QueryRouteResult(
                    route="multi_query", query=question, multi_queries=queries
                )
            return QueryRouteResult(route="original", query=question)
        except Exception:
            logger.exception(
                "apply_route 失败，降级到 original：route=%s question=%r",
                route,
                question,
            )
            return QueryRouteResult(route="original", query=question)

    async def optimize(self, question: str, multi_query_count: int) -> QueryRouteResult:
        """完整 4 选 1：先判定路由，再分发到 apply_route。任何一步失败降级 original。"""
        try:
            route = await self.decide_route(question)
        except Exception:
            logger.exception(
                "query route 判定失败，降级到 original：question=%r", question
            )
            return QueryRouteResult(route="original", query=question)
        return await self.apply_route(question, route, multi_query_count)

    async def contextualize(self, question: str, history: list[Message]) -> str:
        """基于多轮历史把当前问题改写成独立完整问句。
        消解"它/这个/上面提到的"等指代、补省略，让后续 route_query / retrieve
        看到的 query 已经独立可检索。空历史直接回原问题；任何异常 / 改写为空 → 降级回原问题。
        """
        history_text = _format_history_text(history)
        if not history_text:
            return question
        try:
            messages = build_contextualize_messages(
                question=question, history=history_text
            )
            response = await get_chat_model().ainvoke(messages)
            rewritten = _extract_text(response.content).strip()
            return rewritten or question
        except Exception:
            logger.exception(
                "contextualize 调用失败，降级回原问题：question=%r", question
            )
            return question

_rewriter: QueryRewriter | None = None


def get_query_rewriter() -> QueryRewriter:
    global _rewriter
    if _rewriter is None:
        _rewriter = QueryRewriter()
    return _rewriter


def _extract_text(content: str | list[str | dict]) -> str:
    """兼容 langchain ChatModel 的 content 联合类型"""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))

_ROLE_LABEL: dict[MessageRole, str] = {
    MessageRole.USER: "用户",
    MessageRole.ASSISTANT: "助手",
    MessageRole.SYSTEM: "系统",
}
def _format_history_text(history: list[Message]) -> str:
    """把历史 Message 压成给 contextualize prompt 看的纯文本。
    只取 user / assistant，过滤 system；空内容跳过，避免把空消息塞进 prompt 浪费 token。
    """
    lines: list[str] = []
    for msg in history:
        role_label = _ROLE_LABEL.get(msg.role)
        if not role_label or not msg.content.strip():
            continue
        lines.append(f"{role_label}：{msg.content.strip()}")
    return "\n".join(lines)