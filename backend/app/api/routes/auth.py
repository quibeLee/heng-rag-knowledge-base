from fastapi import APIRouter
from app.api.deps import CurrentUser, DbSession
from app.api.schemas.auth import LoginRequest, LoginResponse, MeResponse, UserRead
from app.core.exceptions import UnauthorizedError
from app.services.auth_service import AuthService
from app.services.permission_service import compute_user_permission_tags, is_admin

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse, operation_id="login")
async def login(payload: LoginRequest, session: DbSession) -> LoginResponse:
    service = AuthService(session)
    user = await service.authenticate(payload.username, payload.password)
    if user is None:
        # 统一文案，避免侧信道枚举用户名 vs 密码
        raise UnauthorizedError("用户名或密码错误")
    token = AuthService.issue_token(user)
    return LoginResponse(
        access_token=token,
        user=UserRead.model_validate(user),
        permission_tags=compute_user_permission_tags(user),
        is_admin=is_admin(user),
    )


@router.get("/me", response_model=MeResponse, operation_id="getCurrentUser")
async def me(user: CurrentUser) -> MeResponse:
    """前端启动时 / 路由切换时拉一次，保证角色变更后立即生效。"""
    return MeResponse(
        user=UserRead.model_validate(user),
        permission_tags=compute_user_permission_tags(user),
        is_admin=is_admin(user),
    )
