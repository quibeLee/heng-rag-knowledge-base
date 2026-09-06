from app.core.config import settings
from app.llm.query_rewriter import get_query_rewriter
from app.workflows.rag_state import RAGState
async def route_query(state: RAGState) -> RAGState:
    if not settings.query_route_enabled:
        # 关闭路由：直接走原始查询，保留 normalize_query 透传的 state["query"]
        return {"route": "original"}
    result = await get_query_rewriter().optimize(
        question=state["query"],
        multi_query_count=settings.multi_query_count,
    )
    # query 字段被显式覆盖：rewrite/hyde 路径下用改写文本去向量召回
    update: RAGState = {"route": result.route, "query": result.query}
    if result.rewritten_query is not None:
        update["rewritten_query"] = result.rewritten_query
    if result.hyde_answer is not None:
        update["hyde_answer"] = result.hyde_answer
    if result.multi_queries is not None:
        update["multi_queries"] = result.multi_queries
    return update