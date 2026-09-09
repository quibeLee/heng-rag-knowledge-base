from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import get_logger
from app.core.security import create_access_token, verify_password
from app.db.models import User, UserStatus
from app.db.repositories.user_repo import UserRepository

logger = get_logger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)

    async def authenticate(self, username: str, password: str) -> User | None:
        """登录校验。
        - 用户名不存在 / 密码不对 / 账号禁用 → 统一返回 None
        - 不区分"用户不存在"与"密码不对"避免侧信道枚举用户名
        - 路由层把 None 统一翻译成 UnauthorizedError("用户名或密码错误")
        """
        user = await self.user_repo.get_by_username(username)
        if user is None:
            return None
        if user.status != UserStatus.ACTIVE:
            logger.info("login refused (disabled): username=%s", username)
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    @staticmethod
    def issue_token(user: User) -> str:
        return create_access_token(str(user.id))
