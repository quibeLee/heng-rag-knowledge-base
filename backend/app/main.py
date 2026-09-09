"""应用入口"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.routes import health, documents, chat, graph, evaluations, auth, users, roles
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_observability

from app.db.seed import seed_default_admin
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.mcp_server import knowledge_mcp


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子：启动时做种子初始化，启动期间维护 MCP session manager。
    `mcp.session_manager.run()` 是 FastMCP streamable HTTP 必须的后台任务组，
    没有它工具调用会因 ASGI scope 缺失抛 RuntimeError；与 lifespan 绑定保证
    应用退出时干净收尾。
    """
    logger = get_logger(__name__)
    if not settings.jwt_secret:
        # 教学场景刻意不阻断启动，但提示学员补配
        logger.error("JWT_SECRET 未配置，登录功能将不可用。请在 .env 中设置 JWT_SECRET")
    try:
        await seed_default_admin()
    except Exception:
        logger.exception("种子初始化失败；后续可重新启动重试")
    async with knowledge_mcp.session_manager.run():
        yield


def create_app() -> FastAPI:
    configure_logging()
    configure_observability()
    logger = get_logger(__name__)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(graph.router, prefix="/api")
    app.include_router(evaluations.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(roles.router, prefix="/api")

    # MCP Server 同进程挂载到 /mcp：外部 Agent 用 Streamable HTTP transport 调用，
    # 鉴权复用 Authorization: Bearer JWT
    app.mount("/mcp", knowledge_mcp.streamable_http_app(), name="mcp")

    logger.info("app initialized : %s", settings.app_name)
    return app


app = create_app()
