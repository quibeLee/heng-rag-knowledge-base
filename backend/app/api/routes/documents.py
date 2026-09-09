"""文档管理 API：上传、列表、详情、删除、重试、下载/预览、chunk 浏览、改权限标签。

接入认证与权限：
- 读接口（list / get / chunks / download）→ CurrentUser，按 permission_tags 过滤
- 写接口（upload / delete / retry / update tags）→ CurrentAdmin
"""

import json
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Query, Response, UploadFile

from app.api.deps import CurrentAdmin, CurrentUser, DbSession, RateLimited
from app.api.schemas.documents import (
    DocumentChunkDetail,
    DocumentChunkListResponse,
    DocumentChunkRead,
    DocumentChunkStats,
    DocumentListResponse,
    DocumentPermissionTagsUpdate,
    DocumentRead,
    DocumentStatusValue, IngestionTaskRead,
)
from app.db.models import DocumentStatus, Document
from app.services.document_service import DocumentService
from app.services.permission_service import (
    compute_user_permission_tags,
    is_admin,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# DOCX 即便 ?download=0 也强制 attachment：浏览器无法内联渲染 DOCX
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _viewer_tags(user) -> list[str] | None:  # type: ignore[no-untyped-def]
    """admin 视角传 None（service 内部转为不限）；普通用户传合并后的有效标签。"""
    return None if is_admin(user) else compute_user_permission_tags(user)


@router.post("", response_model=DocumentRead, status_code=201, operation_id="uploadDocument")
async def upload_document(
        admin: CurrentAdmin,
        _rate_limit: RateLimited,
        session: DbSession,
        file: UploadFile = File(..., description="待上传文档（PDF / DOCX / Markdown / HTML）"),
        permission_tags: str | None = Form(
            default=None,
            description='JSON 数组字符串，例如 ["public","hr"]；空 / 不传视为公开',
        ),
) -> DocumentRead:
    """上传文档：写入 COS、落库后立即返回，解析与向量化通过 Celery 任务异步进行。"""
    tags: list[str] = []
    if permission_tags:
        try:
            parsed = json.loads(permission_tags)
        except json.JSONDecodeError:
            # 直接当做单标签字符串吞下，提升使用容错
            parsed = [permission_tags]
        if isinstance(parsed, list):
            tags = [str(t) for t in parsed]

    service = DocumentService(session)
    document = await service.upload(
        file,
        created_by=admin.id,
        permission_tags=tags,
    )
    return await _to_document_read(document, service)


@router.get("", response_model=DocumentListResponse, operation_id="listDocuments")
async def list_documents(
        user: CurrentUser,
        session: DbSession,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: DocumentStatusValue | None = Query(None, description="按文档状态筛选"),
) -> DocumentListResponse:
    service = DocumentService(session)
    items, total = await service.list_documents(
        page,
        page_size,
        status=DocumentStatus(status) if status else None,
        permission_tags=_viewer_tags(user),
    )
    return DocumentListResponse(
        items=[await _to_document_read(d, service) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentRead, operation_id="getDocument")
async def get_document(
        document_id: UUID,
        user: CurrentUser,
        session: DbSession,
) -> DocumentRead:
    service = DocumentService(session)
    document = await service.get(document_id, permission_tags=_viewer_tags(user))
    return await _to_document_read(document, service)


@router.delete("/{document_id}", status_code=204, operation_id="deleteDocument")
async def delete_document(
        _: CurrentAdmin,
        document_id: UUID,
        session: DbSession,
) -> Response:
    service = DocumentService(session)
    await service.delete(document_id)
    return Response(status_code=204)


@router.post(
    "/{document_id}/retry",
    response_model=DocumentRead,
    operation_id="retryDocument",
)
async def retry_document(
        _: CurrentAdmin,
        document_id: UUID,
        session: DbSession,
) -> DocumentRead:
    service = DocumentService(session)
    document = await service.retry(document_id)
    return await _to_document_read(document, service)


@router.patch(
    "/{document_id}/permission-tags",
    response_model=DocumentRead,
    operation_id="updateDocumentPermissionTags",
)
async def update_permission_tags(
        _: CurrentAdmin,
        document_id: UUID,
        session: DbSession,
        payload: DocumentPermissionTagsUpdate,
) -> DocumentRead:
    service = DocumentService(session)
    document = await service.update_permission_tags(
        document_id, payload.permission_tags
    )
    return await _to_document_read(document, service)


@router.get("/{document_id}/file", operation_id="downloadDocument")
async def download_document(
        document_id: UUID,
        session: DbSession,
        download: int = Query(0, ge=0, le=1, description="1=强制下载, 0=尝试内联预览"),
        token: str | None = Query(None, description="Bearer token（iframe/新窗口无法带 header 时使用）"),
        authorization: str | None = Header(None, alias="Authorization"),
) -> Response:
    """返回文档原始字节。

    支持两种认证方式（优先 header）：
    - Authorization header（常规 fetch 请求）
    - ?token= query 参数（iframe / 浏览器直接打开时无法带 header）
    """
    from app.api.deps import get_current_user

    effective_auth = authorization or (f"Bearer {token}" if token else None)
    user = await get_current_user(session, effective_auth)

    service = DocumentService(session)
    document = await service.get(document_id, permission_tags=_viewer_tags(user))
    content = await service.file_service.download(document.cos_object_key)

    force_attachment = download == 1 or document.mime_type == _DOCX_MIME
    disposition = "attachment" if force_attachment else "inline"
    # RFC 5987 编码非 ASCII 文件名，避免中文文件名报错
    filename_quoted = quote(document.name, safe="")

    return Response(
        content=content,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''{filename_quoted}"
            ),
        },
    )


@router.get(
    "/{document_id}/chunks",
    response_model=DocumentChunkListResponse,
    operation_id="listDocumentChunks",
)
async def list_document_chunks(
        document_id: UUID,
        user: CurrentUser,
        session: DbSession,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
) -> DocumentChunkListResponse:
    service = DocumentService(session)
    items, total, stats = await service.list_chunks(
        document_id, page, page_size, permission_tags=_viewer_tags(user)
    )
    return DocumentChunkListResponse(
        items=[DocumentChunkRead.from_orm_chunk(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        stats=DocumentChunkStats(
            total=stats.total,
            avg_length=stats.avg_length,
            min_length=stats.min_length,
            max_length=stats.max_length,
        )
        if stats is not None
        else None,
    )


@router.get(
    "/{document_id}/chunks/{chunk_id}",
    response_model=DocumentChunkDetail,
    operation_id="getDocumentChunk",
)
async def get_document_chunk(
        document_id: UUID,
        chunk_id: UUID,
        user: CurrentUser,
        session: DbSession,
) -> DocumentChunkDetail:
    service = DocumentService(session)
    chunk = await service.get_chunk(
        document_id, chunk_id, permission_tags=_viewer_tags(user)
    )
    return DocumentChunkDetail.from_orm_chunk(chunk)

@router.post(
    "/{document_id}/reindex",
    response_model=DocumentRead,
    operation_id="reindexDocument",
)
async def reindex_document(
    _: CurrentAdmin,
    _rate_limit: RateLimited,
    document_id: UUID,
    session: DbSession,
    file: UploadFile = File(
        ..., description="新版本文件（MIME 必须与原文档一致）"
    ),
) -> DocumentRead:
    """上传新版本文件，触发按 chunk_hash 对齐的增量重建。"""
    service = DocumentService(session)
    document = await service.reindex(document_id, file)
    return await _to_document_read(document, service)

async def _to_document_read(
    document: Document, service: DocumentService
) -> DocumentRead:
    """组装 DocumentRead：附带 latest_task，前端轮询时直接展示任务进度卡片。"""
    latest = await service.get_latest_task(document.id)
    return DocumentRead.model_validate(
        {
            **{c.name: getattr(document, c.name) for c in document.__table__.columns},
            "latest_task": IngestionTaskRead.model_validate(latest)
            if latest is not None
            else None,
        }
    )