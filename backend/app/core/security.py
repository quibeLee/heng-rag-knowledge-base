from datetime import UTC, datetime, timedelta
import bcrypt
import jwt
from app.core.config import settings
from app.core.exceptions import UnauthorizedError

"""认证安全工具：密码哈希、JWT 签发与校验。

bcrypt 直接调，不用 passlib：
- passlib 多年未维护，对 bcrypt 4+/5+ 不兼容（会报 __about__ 属性缺失警告）
- bcrypt 单库 API 已经足够简洁，没必要再套一层

JWT 用 PyJWT：HS256 对称签名；token 仅放 sub (user_id) + exp，
其余用户信息（角色、权限标签）每次按 token 拉一次最新值，避免角色变更后旧 token 仍可用。
"""

def hash_password(plain: str) -> str:
    """bcrypt 默认 cost=12，单次哈希约 200ms，足够防御彩虹表 + 暴力破解。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # 历史脏数据导致的非法 hash 串，按"密码不对"处理即可，不要抛
        return False


def create_access_token(subject: str) -> str:
    """signed JWT。subject 约定放 user_id 字符串。"""
    if not settings.jwt_secret:
        # 启动期已经打过 ERROR；这里再兜一道，避免空 secret 签出"任何人都能伪造"的 token
        raise UnauthorizedError("服务端未配置 JWT secret，无法签发 token")
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """返回 token 中 sub 字段（user_id 字符串）；任何失败都转 UnauthorizedError。"""
    if not settings.jwt_secret:
        raise UnauthorizedError("服务端未配置 JWT secret")
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("无效的访问凭证") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise UnauthorizedError("无效的访问凭证")
    return subject
