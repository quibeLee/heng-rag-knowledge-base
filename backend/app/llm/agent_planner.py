import json
from dataclasses import dataclass
from typing import Literal, get_args
from app.core.logging import get_logger
from app.llm.models import get_chat_model
from app.llm.prompts import build_agent_plan_messages
from app.workflows.rag_state import QueryRoute

logger = get_logger(__name__)
AgentAction = Literal["proceed", "rewrite_query", "switch_route", "refuse"]
_VALID_ACTIONS: tuple[str, ...] = get_args(AgentAction)
_VALID_ROUTES: tuple[str, ...] = get_args(QueryRoute)


@dataclass(frozen=True)
class AgentDecision:
    """Agent 一轮决策结果。
       new_query / new_route 仅在对应 action 下有意义；其余 action 下为 None。
    """
    action: AgentAction
    reason: str
    new_query: str | None = None
    new_route: QueryRoute | None = None


class AgentPlanner:
    """LLM 决策的薄封装。"""

    async def plan(
            self,
            question: str,
            current_route: QueryRoute,
            current_query: str,
            previous_steps: list[dict],
    ) -> AgentDecision:
        history = _format_history(previous_steps)
        messages = build_agent_plan_messages(
            question=question,
            current_query=current_query,
            current_route=current_route,
            history=history,
        )
        try:
            response = await get_chat_model().ainvoke(messages)
            raw = _extract_text(response.content).strip()
            return _parse_decision(raw)
        except Exception:
            logger.exception("agent planner 调用失败，降级 proceed：question=%r", question)
            return AgentDecision(action="proceed", reason="planner_exception")


_planner: AgentPlanner | None = None


def get_agent_planner() -> AgentPlanner:
    global _planner
    if _planner is None:
        _planner = AgentPlanner()
    return _planner


def _format_history(steps: list[dict]) -> str:
    """把历史 agent_steps 压成一段可读文本给 LLM 看。"""
    if not steps:
        return "（无）"
    lines: list[str] = []
    for step in steps:
        lines.append(
            f"- round {step.get('round')}: action={step.get('action')}, "
            f"route={step.get('route')}, query={step.get('query')!r}, "
            f"retrieved_count={step.get('retrieved_count')}, "
            f"top_score={step.get('top_score')}, "
            f"sufficient={step.get('sufficient')}"
        )
    return "\n".join(lines)


def _parse_decision(raw: str) -> AgentDecision:
    """解析 LLM JSON 输出。任何字段非法 → 降级 proceed。"""
    # 容错：模型偶尔会包一层 ```json ... ```
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("agent planner JSON 解析失败，降级 proceed：raw=%r", raw)
        return AgentDecision(action="proceed", reason="planner_parse_failed")
    if not isinstance(data, dict):
        return AgentDecision(action="proceed", reason="planner_parse_failed")
    action = str(data.get("action", "")).strip().lower()
    if action not in _VALID_ACTIONS:
        logger.warning("agent planner 返回非法 action=%r，降级 proceed", action)
        return AgentDecision(action="proceed", reason="planner_invalid_action")
    reason = str(data.get("reason") or "").strip() or "(no reason)"
    new_query_raw = data.get("new_query")
    new_query = (
        str(new_query_raw).strip()
        if isinstance(new_query_raw, str) and new_query_raw.strip()
        else None
    )
    new_route_raw = data.get("new_route")
    new_route: QueryRoute | None = None
    if isinstance(new_route_raw, str) and new_route_raw.strip().lower() in _VALID_ROUTES:
        new_route = new_route_raw.strip().lower()  # type: ignore[assignment]
    # rewrite_query 必须带 new_query；缺失则降级 proceed，避免空字符串去 embedding
    if action == "rewrite_query" and not new_query:
        logger.warning("agent planner action=rewrite_query 缺少 new_query，降级 proceed")
        return AgentDecision(action="proceed", reason="planner_missing_query")
    # switch_route 必须带 new_route
    if action == "switch_route" and new_route is None:
        logger.warning("agent planner action=switch_route 缺少 new_route，降级 proceed")
        return AgentDecision(action="proceed", reason="planner_missing_route")
    return AgentDecision(
        action=action,  # type: ignore[arg-type]
        reason=reason,
        new_query=new_query,
        new_route=new_route,
    )


def _extract_text(content: str | list[str | dict]) -> str:
    """兼容 langchain ChatModel 的 content 联合类型。"""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))
