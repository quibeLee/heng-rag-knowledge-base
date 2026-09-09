# 通配权限标签：含义"无视权限过滤"，admin 默认持有
from app.db.models import User

WILDCARD_PERMISSION_TAG = "*"
# 内置角色名
ADMIN_ROLE_NAME = "admin"
def compute_user_permission_tags(user: User) -> list[str]:
    """合并用户所有角色的 permission_tags 并去重。
    - 任一角色持有 "*" → 直接返回 ["*"]，下游 SQL 不加权限过滤
    - 多角色叠加用集合去重；顺序对检索逻辑无意义，但稳定排序便于 debug
    """
    merged: set[str] = set()
    for role in user.roles:
        for tag in role.permission_tags:
            if tag == WILDCARD_PERMISSION_TAG:
                return [WILDCARD_PERMISSION_TAG]
            merged.add(tag)
    return sorted(merged)
def is_admin(user: User) -> bool:
    """是否拥有 admin 角色（按角色名识别，不依赖通配标签，便于学员理解）。"""
    return any(role.name == ADMIN_ROLE_NAME for role in user.roles)