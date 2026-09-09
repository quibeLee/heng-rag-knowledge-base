"""MCP 工具的鉴权 helper。

复用第 11 章的 JWT 体系：客户端走 `POST /api/auth/login` 拿到 token 后，
通过 `Authorization: Bearer <token>` 头部带给 MCP server。

实现说明：
- Streamable HTTP 模式下，starlette 的 Request 已经被 SDK 透传到
  `ctx.request_context.request`（参考 GitHub
  modelcontextprotocol/python-sdk#750 的 closing answer）
- 所有失败路径统一抛 `ToolError`，由 SDK 包装成 CallToolResult{isError: true}
  返回给外部 Agent；不区分"token 缺失 / 签名错 / 用户禁用"细节，避免侧信道
"""

from uuid import UUID

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from app.core.exceptions import AppException, UnauthorizedError
from app.core.security import decode_access_token
from app.db.models import User, UserStatus
from app.db.repositories.user_repo import UserRepository
from app.db.session import AsyncSessionLocal
from app.services.permission_service import is_admin


def _parse_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise UnauthorizedError("请先登录")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("无效的访问凭证")
    return token


async def resolve_current_user(ctx: Context) -> User:
    """从 MCP Context 取出 Bearer token 并查到活跃用户。

    与 REST `get_current_user` 行为完全一致；唯一区别是失败时抛
    `ToolError` 而不是 HTTP 401：MCP 协议没有 401 概念，错误信息走
    工具结果通道返回。
    """
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        # stdio / 其他无 HTTP 上下文的传输模式：项目只提供 streamable HTTP
        raise ToolError("当前传输不支持鉴权")

    try:
        token = _parse_bearer_token(request.headers.get("authorization"))
        subject = decode_access_token(token)
        user_id = UUID(subject)
    except (AppException, ValueError) as exc:
        # AppException 含 UnauthorizedError；ValueError 是 sub 非 UUID
        raise ToolError(_user_facing_message(exc, default="无效的访问凭证")) from exc

    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_id(user_id)

    if user is None:
        raise ToolError("用户不存在或已被删除")
    if user.status != UserStatus.ACTIVE:
        raise ToolError("账号已被禁用")
    return user


def require_admin(user: User) -> None:
    """admin-only 工具入口处调用；非 admin 直接 ToolError。"""
    if not is_admin(user):
        raise ToolError("仅管理员可调用此工具")


def _user_facing_message(exc: Exception, *, default: str) -> str:
    """统一把 AppException.message 透出给 Agent；其它异常用兜底文案。"""
    if isinstance(exc, AppException):
        return exc.message
    return default
