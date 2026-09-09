"""LangSmith 可观测性接入。
设计意图：
- 把 langsmith 的具体 SDK 调用收敛到一个文件，让业务代码只看到 `@traceable`
  装饰器和 `get_current_trace_id()` 两个稳定符号
- 启动期把 `settings` 同步成 LangSmith 官方环境变量。langsmith SDK 内部读
  的是 env vars（`LANGSMITH_TRACING` / `LANGSMITH_API_KEY` ...），不读我们
  的 `Settings`，所以必须在 `create_app` 阶段做一次显式同步
- 未启用时强制写 `LANGSMITH_TRACING=false`，避免 SDK 在缺 key 情况下尝试
  上报 trace 报错刷屏
"""
import os
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def configure_observability() -> None:
    """把 settings 写到 LangSmith 官方环境变量，应用启动时调用一次即可。
    - 启用：写 LANGSMITH_TRACING=true + API key / project / endpoint
    - 关闭：强制写 LANGSMITH_TRACING=false，避免 SDK 走默认开启路径
    """
    if settings.observability_enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
        logger.info(
            "LangSmith tracing enabled: project=%s endpoint=%s",
            settings.langsmith_project,
            settings.langsmith_endpoint,
        )
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
        logger.info("LangSmith tracing disabled (no LANGSMITH_API_KEY or switch off)")


def get_current_trace_id() -> str | None:
    """读取当前 @traceable 上下文中的 trace_id。
    - 未启用 LangSmith / 不在 traceable 上下文 / 任何异常 → 返回 None
    - 永远不抛错：可观测性是"加分项"，不能因为 trace SDK 抖动阻断问答
    """
    if not settings.observability_enabled:
        return None
    try:
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run is None:
            return None
        return str(run.trace_id)
    except Exception:
        logger.warning("get_current_trace_id 异常，返回 None", exc_info=True)
        return None

def build_trace_url(trace_id: str | None) -> str | None:
    """把 trace_id 拼成 LangSmith UI 的 run 详情页 URL。
    需要用户在 .env 配置 LANGSMITH_RUN_URL_PREFIX（带 org / project 信息），
    未配置时返回 None，前端只展示复制按钮、不展示跳转链接。
    """
    if not trace_id or not settings.langsmith_run_url_prefix:
        return None
    return f"{settings.langsmith_run_url_prefix.rstrip('/')}/r/{trace_id}"
