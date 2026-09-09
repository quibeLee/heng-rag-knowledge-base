from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
# 与 app.db.models.UserStatus 同步
UserStatusValue = Literal["active", "disabled"]
class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str
    permission_tags: list[str] = Field(default_factory=list)
    created_at: datetime
class UserRead(BaseModel):
    """用户响应。role 列表里只展示必要字段，权限标签由前端从 roles 推。"""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    display_name: str
    status: UserStatusValue
    roles: list[RoleRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class LoginRequest(BaseModel):
    username: str
    password: str
class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead
    permission_tags: list[str] = Field(default_factory=list)
    is_admin: bool
class MeResponse(BaseModel):
    """当前登录用户视图：用户基础信息 + 合并后的有效权限标签 + 是否管理员。"""
    user: UserRead
    permission_tags: list[str] = Field(default_factory=list)
    is_admin: bool