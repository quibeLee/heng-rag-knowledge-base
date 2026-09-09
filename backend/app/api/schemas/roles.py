from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    permission_tags: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """name 字段刻意不暴露：策略代码以角色名为锚（"admin"），不允许改名。"""
    description: str | None = Field(default=None, max_length=256)
    permission_tags: list[str] | None = None
