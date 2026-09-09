from uuid import UUID
from pydantic import BaseModel, Field
from app.api.schemas.auth import UserRead, UserStatusValue


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    role_ids: list[UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    """PATCH 请求体；字段均可选。
    password 字段如果传非空字符串则重置密码；传 None 不动密码。
    """
    display_name: str | None = Field(default=None, max_length=128)
    status: UserStatusValue | None = None
    password: str | None = Field(default=None, min_length=4, max_length=128)


class AssignRolesRequest(BaseModel):
    role_ids: list[UUID] = Field(default_factory=list)


class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
