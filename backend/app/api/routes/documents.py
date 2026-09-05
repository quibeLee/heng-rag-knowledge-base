from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, File, Query, Response, UploadFile
from app.api.deps import DbSession
from app.api.schemas.documents import (
    DocumentChunkDetail,
    DocumentChunkListResponse,
    DocumentChunkRead,
    DocumentChunkStats,
    DocumentListResponse,
    DocumentRead,
    DocumentStatusValue,
)
from app.db.models import DocumentStatus
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentRead, status_code=201, operation_id="uploadDocument")
async def upload_document(
        session: DbSession,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(..., description="待上传文档（PDF / DOCX / Markdown / HTML）"),
) -> DocumentRead:
    """上传文档：写入 COS、落库后立即返回，解析与向量化通过 BackgroundTasks 异步进行。"""
    service = DocumentService(session)
    document = await service.upload(file, background_tasks)
    return DocumentRead.model_validate(document)


@router.get("", response_model=DocumentListResponse, operation_id="listDocuments")
async def list_documents(
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
    )
    return DocumentListResponse(
        items=[DocumentRead.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentRead, operation_id="getDocument")
async def get_document(document_id: UUID, session: DbSession) -> DocumentRead:
    service = DocumentService(session)
    document = await service.get(document_id)
    return DocumentRead.model_validate(document)


@router.delete("/{document_id}", status_code=204, operation_id="deleteDocument")
async def delete_document(document_id: UUID, session: DbSession) -> Response:
    service = DocumentService(session)
    await service.delete(document_id)
    return Response(status_code=204)


@router.post(
    "/{document_id}/retry",
    response_model=DocumentRead,
    operation_id="retryDocument",
)
async def retry_document(
        document_id: UUID,
        session: DbSession,
        background_tasks: BackgroundTasks,
) -> DocumentRead:
    service = DocumentService(session)
    document = await service.retry(document_id, background_tasks)
    return DocumentRead.model_validate(document)


# DOCX 即便 ?download=0 也强制 attachment：浏览器无法内联渲染 DOCX
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{document_id}/file", operation_id="downloadDocument")
async def download_document(
        document_id: UUID,
        session: DbSession,
        download: int = Query(0, ge=0, le=1, description="1=强制下载, 0=尝试内联预览"),
) -> Response:
    """返回文档原始字节。
    - PDF / HTML / Markdown：可在浏览器内联预览
    - DOCX：浏览器无法渲染，强制 attachment
    """
    service = DocumentService(session)
    document = await service.get(document_id)
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
        session: DbSession,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
) -> DocumentChunkListResponse:
    service = DocumentService(session)
    items, total, stats = await service.list_chunks(document_id, page, page_size)
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
        session: DbSession,
) -> DocumentChunkDetail:
    service = DocumentService(session)
    chunk = await service.get_chunk(document_id, chunk_id)
    return DocumentChunkDetail.from_orm_chunk(chunk)
