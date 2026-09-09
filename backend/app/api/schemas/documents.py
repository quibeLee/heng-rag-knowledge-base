"""文档相关请求 / 响应模型。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 与 app.db.models.DocumentStatus 同步；用 Literal 让前端 openapi-typescript
# 生成精确的字面量联合类型，而不是宽 string
DocumentStatusValue = Literal["uploading", "parsing", "indexing", "ready", "failed"]

# chunk 列表里只回截断后的摘要，避免长 chunk 撑爆响应；查看完整内容走详情接口
_CONTENT_EXCERPT_LIMIT = 100


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    file_hash: str
    mime_type: str
    size: int
    status: DocumentStatusValue
    error_message: str | None = None
    # 空数组视为"公开"；非空数组与用户有效权限标签做重叠匹配
    permission_tags: list[str] = Field(default_factory=list)
    # 上传者 user_id；用户被硬删后置 None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class DocumentPermissionTagsUpdate(BaseModel):
    """admin 改文档可见性标签的请求体。"""

    permission_tags: list[str] = Field(default_factory=list)


class DocumentChunkRead(BaseModel):
    """chunk 列表项。content_excerpt 已在 API 层截断到固定长度。"""

    id: UUID
    chunk_index: int
    page_no: int | None = None
    section_path: str | None = None
    content_excerpt: str
    char_count: int
    chunk_hash: str

    @classmethod
    def from_orm_chunk(cls, chunk) -> "DocumentChunkRead":  # type: ignore[no-untyped-def]
        content = chunk.content or ""
        excerpt = content[:_CONTENT_EXCERPT_LIMIT]
        if len(content) > _CONTENT_EXCERPT_LIMIT:
            excerpt += "..."
        return cls(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            page_no=chunk.page_no,
            section_path=chunk.section_path,
            content_excerpt=excerpt,
            char_count=len(content),
            chunk_hash=chunk.chunk_hash,
        )


class DocumentChunkStats(BaseModel):
    """切分统计：展示 chunk_size / overlap 配置对切分结果的影响。"""

    total: int
    avg_length: int
    min_length: int
    max_length: int


class DocumentChunkListResponse(BaseModel):
    items: list[DocumentChunkRead]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    stats: DocumentChunkStats | None = None


class DocumentChunkDetail(BaseModel):
    """chunk 详情：返回完整 content。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    page_no: int | None = None
    section_path: str | None = None
    content: str
    char_count: int
    chunk_hash: str
    created_at: datetime

    @classmethod
    def from_orm_chunk(cls, chunk) -> "DocumentChunkDetail":  # type: ignore[no-untyped-def]
        return cls(
            id=chunk.id,
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            page_no=chunk.page_no,
            section_path=chunk.section_path,
            content=chunk.content,
            char_count=len(chunk.content or ""),
            chunk_hash=chunk.chunk_hash,
            created_at=chunk.created_at,
        )