"""MCP 工具入参 / 出参 Pydantic 模型。

与 REST `app/api/schemas/` 故意保持独立，因为 MCP 协议把 schema 直接暴露给
外部 Agent，字段命名与可见性更接近"对外契约"，不能随 REST 改 ORM 时联动。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.documents import DocumentStatusValue, IngestionTaskStatusValue


class MCPCitation(BaseModel):
    """问答引用快照。与 SSE / 历史接口的 citation 形状对齐，但只保留外部
    Agent 真正需要的字段，避免把 retrieval_meta / rerank_score 等内部调试
    元数据塞给 Agent。
    """

    ordinal: int = Field(description="prompt 中给 LLM 的「片段 N」编号，从 1 开始")
    document_id: UUID
    document_name: str
    page_no: int | None = None
    section_path: str | None = None
    quote: str = Field(description="该片段在 prompt 里的原文")


class MCPAskAnswer(BaseModel):
    """ask_knowledge_base 出参。"""

    answer: str
    refused: bool = Field(description="是否触发拒答（命中阈值不足或答案校验失败）")
    citations: list[MCPCitation] = Field(default_factory=list)
    trace_id: str | None = Field(
        default=None,
        description="LangSmith trace_id，未启用观测时为空",
    )


class MCPUploadResult(BaseModel):
    """upload_document 出参。"""

    document_id: UUID
    name: str
    status: DocumentStatusValue
    version: int
    file_hash: str = Field(
        description="sha256；文件级幂等键，相同 hash 复用现有文档"
    )


class MCPDocumentItem(BaseModel):
    """list_documents 列表项。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: DocumentStatusValue
    mime_type: str
    size: int
    version: int
    permission_tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MCPDocumentList(BaseModel):
    """list_documents 出参，标准分页结构。"""

    items: list[MCPDocumentItem]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class MCPDocumentStatus(BaseModel):
    """get_document_status 出参。

    `latest_task_*` 字段允许外部 Agent 直接观察 ingest / reindex 进度，
    避免它们再多调一次 list_documents。
    """

    document_id: UUID
    name: str
    status: DocumentStatusValue
    version: int
    error_message: str | None = None
    latest_task_type: Literal["ingest", "reindex"] | None = None
    latest_task_status: IngestionTaskStatusValue | None = None
    latest_task_progress_total: int | None = None
    latest_task_progress_done: int | None = None
    latest_task_error_message: str | None = None


class MCPStats(BaseModel):
    """get_knowledge_base_stats 出参。

    严格按调用者权限范围统计；admin 视角看全量，普通用户只看自己有权访问的。
    """

    document_count: int
    chunk_count: int
    last_indexed_at: datetime | None = Field(
        default=None,
        description="最近一次进入 ready 状态的文档时间；库为空时返回 null",
    )
