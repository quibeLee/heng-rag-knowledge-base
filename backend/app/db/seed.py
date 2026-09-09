from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.models import Role, User, UserStatus
from app.db.repositories.role_repo import RoleRepository
from app.db.repositories.user_repo import UserRepository
from app.db.session import AsyncSessionLocal
from app.services.permission_service import WILDCARD_PERMISSION_TAG
logger = get_logger(__name__)
# 内置角色定义
_BUILTIN_ROLES: list[dict] = [
    {
        "name": "admin",
        "description": "系统管理员",
        # 通配标签：检索 SQL 不附加权限过滤
        "permission_tags": [WILDCARD_PERMISSION_TAG],
    },
    {
        "name": "user",
        "description": "普通用户",
        # 默认仅能访问 "public" 标签的文档；空 permission_tags 的存量文档也会被视为公开
        "permission_tags": ["public"],
    },
]
async def seed_default_admin() -> None:
    """库内无用户时建 admin 角色 + user 角色 + admin 用户（关联 admin 角色）。"""
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        if await user_repo.count_all() > 0:
            return
        logger.info("seeding default admin user and built-in roles")
        role_repo = RoleRepository(session)
        roles_by_name: dict[str, Role] = {}
        for spec in _BUILTIN_ROLES:
            existing = await role_repo.get_by_name(spec["name"])
            if existing is not None:
                roles_by_name[spec["name"]] = existing
                continue
            role = Role(
                name=spec["name"],
                description=spec["description"],
                permission_tags=list(spec["permission_tags"]),
            )
            await role_repo.add(role)
            roles_by_name[spec["name"]] = role
        admin_user = User(
            username=settings.default_admin_username,
            password_hash=hash_password(settings.default_admin_password),
            display_name=settings.default_admin_display_name,
            status=UserStatus.ACTIVE,
        )
        admin_user.roles = [roles_by_name["admin"]]
        await user_repo.add(admin_user)
        await session.commit()