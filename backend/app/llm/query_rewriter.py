from dataclasses import dataclass
from typing import get_args
from app.core.logging import get_logger
from app.llm.models import get_chat_model
from app.llm.prompts import (
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

    async def optimize(self, question: str, multi_query_count: int) -> QueryRouteResult:
        """按 route 分发并填充结果。任何一步失败都安全降级到 original。"""
        try:
            route = await self.decide_route(question)
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
                "query optimize 失败，降级到 original：question=%r", question
            )
            return QueryRouteResult(route="original", query=question)

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