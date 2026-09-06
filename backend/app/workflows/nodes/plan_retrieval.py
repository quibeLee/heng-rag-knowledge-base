from app.core.config import settings
from app.llm.agent_planner import get_agent_planner
from app.llm.prompts import REFUSAL_ANSWER
from app.llm.query_rewriter import get_query_rewriter
from app.workflows.rag_state import QueryRoute, RAGState


async def plan_retrieval(state: RAGState) -> RAGState:
    steps = list(state.get("agent_steps", []))
    current_route: QueryRoute = state.get("route", "original")
    current_query = state.get("query") or state["question"]
    # 第 1 轮：route_query 已经决定好了 route/query，无需再调 LLM
    if not steps:
        steps.append(
            {
                "round": 1,
                "action": "initial",
                "reason": "首轮检索沿用 route_query 决策",
                "route": current_route,
                "query": current_query,
            }
        )
        return {"agent_steps": steps}
    # 后续轮：由 LLM planner 决定如何重试
    decision = await get_agent_planner().plan(
        question=state["question"],
        current_route=current_route,
        current_query=current_query,
        previous_steps=steps,
    )
    update: RAGState = {}
    new_route = current_route
    new_query = current_query
    if decision.action == "rewrite_query" and decision.new_query:
        # 改 query 同时把 route 强制重置为 original：上一轮可能是 multi_query，
        # 残留的 multi_queries 会让 retrieve 忽略本轮新 query，必须清干净
        new_query = decision.new_query
        new_route = "original"
        update["query"] = new_query
        update["route"] = new_route
        update["rewritten_query"] = None
        update["hyde_answer"] = None
        update["multi_queries"] = None
    elif decision.action == "switch_route" and decision.new_route:
        # 真正切换：调 QueryRewriter 补齐目标路由对应字段，避免只换标签不换行为
        rewriter = get_query_rewriter()
        result = await rewriter.apply_route(
            question=state["question"],
            route=decision.new_route,
            multi_query_count=settings.multi_query_count,
        )
        new_route = result.route
        new_query = result.query
        update["route"] = new_route
        update["query"] = new_query
        update["rewritten_query"] = result.rewritten_query
        update["hyde_answer"] = result.hyde_answer
        update["multi_queries"] = result.multi_queries
    steps.append(
        {
            "round": len(steps) + 1,
            "action": decision.action,
            "reason": decision.reason,
            "route": new_route,
            "query": new_query,
        }
    )
    update["agent_steps"] = steps
    # refuse 时直接终止图：retrieve 不再跑，answer 也要在这里兜底，
    # 否则 service 看到 refused=True 但 state["answer"] 缺失会发送空 token
    if decision.action == "refuse":
        update["refused"] = True
        update["answer"] = REFUSAL_ANSWER
    return update
