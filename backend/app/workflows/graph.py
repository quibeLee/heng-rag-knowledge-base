from langgraph.graph import END, START, StateGraph
from app.core.config import settings
from app.core.logging import get_logger
from app.workflows.nodes import (
    normalize_query,
    observe_context,
    plan_retrieval,
    retrieve,
    route_query,
)
from app.workflows.rag_state import RAGState
logger = get_logger(__name__)
def _after_plan(state: RAGState) -> str:
    """planner 决策为 refuse 时直接结束，避免再做无意义的检索。"""
    if state.get("agent_steps"):
        last_action = state["agent_steps"][-1].get("action")
        if last_action == "refuse":
            return "end"
    return "retrieve"
def _after_observe(state: RAGState) -> str:
    """observe 后决定继续循环还是结束。
    结束条件（任一即停）：
    - 关闭了 agent loop（退化为单轮）
    - 本轮已足够（context_sufficient=True）
    - 达到 agent_max_rounds 上限
    """
    if not settings.agent_loop_enabled:
        return "end"
    if state.get("context_sufficient"):
        return "end"
    if state.get("retrieval_round", 0) >= settings.agent_max_rounds:
        return "end"
    return "plan"

def _build_graph():
    builder = StateGraph(RAGState)
    builder.add_node("normalize_query", normalize_query)
    builder.add_node("route_query", route_query)
    builder.add_node("plan_retrieval", plan_retrieval)
    builder.add_node("retrieve", retrieve)
    builder.add_node("observe_context", observe_context)
    builder.add_edge(START, "normalize_query")
    builder.add_edge("normalize_query", "route_query")
    builder.add_edge("route_query", "plan_retrieval")
    builder.add_conditional_edges(
        "plan_retrieval", _after_plan, {"retrieve": "retrieve", "end": END}
    )
    builder.add_edge("retrieve", "observe_context")
    builder.add_conditional_edges(
        "observe_context",
        _after_observe,
        {"plan": "plan_retrieval", "end": END},
    )
    return builder.compile()
_rag_graph = _build_graph()
def get_rag_graph():
    """对外暴露已编译好的子图；模块加载时一次编译，请求里直接复用。"""
    return _rag_graph