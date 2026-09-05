"""健康检查路由:验证后端、数据库、COS 是否配置正确"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.storage.cos_client import get_cos_client

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

HealthStatusValue = Literal["ok", "error", "not_configured"]

class HealthStatus(BaseModel):
    status: HealthStatusValue
    detail: str | None = None

@router.get("", response_model=HealthStatus, operation_id="healthApp")
async def health() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/db", response_model=HealthStatus, operation_id="healthDb")
async def health_db(session: DbSession) -> HealthStatus:
    """跑一句 SELECT 1 验证 DB 连通性。"""
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar_one()
        return HealthStatus(status="ok")
    except Exception as exc:
        logger.exception("db health check failed")
        return HealthStatus(status="error", detail=str(exc))


@router.get("/cos", response_model=HealthStatus, operation_id="healthCos")
async def health_cos() -> HealthStatus:
    """检查 COS 连通性；未配置时返回 not_configured 而非 500。"""
    if not settings.cos_configured:
        return HealthStatus(status="not_configured", detail="COS 凭据未在 .env 中配置")

    try:
        ok = await get_cos_client().ping()
    except Exception as exc:
        logger.exception("cos health check failed")
        return HealthStatus(status="error", detail=str(exc))

    return HealthStatus(status="ok") if ok else HealthStatus(status="error", detail="head_bucket 失败")
