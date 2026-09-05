import hashlib
from pathlib import PurePath
from uuid import UUID
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk, DocumentStatus
from app.db.repositories.chunk_repo import (
    ChunkStats,
    DocumentChunkRepository,
)
from app.db.repositories.document_repo import DocumentRepository
from app.ingestion.pipeline import ingest_document
from app.storage.file_service import FileService, get_file_service

# 受支持的 MIME 类型。Docling 还支持其他格式，本章先收敛为常见四种以便课件演示
_ACCEPTED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
}
_ACCEPTED_SUFFIXES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}


def _resolve_mime_and_suffix(file: UploadFile) -> tuple[str, str]:
    """根据 UploadFile 的 content_type 和扩展名共同判定。
    浏览器上传 .md 时常给 application/octet-stream，所以扩展名优先级更高。
    """
    suffix = PurePath(file.filename or "").suffix.lower()
    if suffix in _ACCEPTED_SUFFIXES:
        return _ACCEPTED_SUFFIXES[suffix], suffix
    mime = file.content_type or ""
    if mime in _ACCEPTED_MIME_TYPES:
        return mime, _ACCEPTED_MIME_TYPES[mime]
    raise ValidationError(
        f"不支持的文件类型：{file.filename}（{mime or '未知'}）。"
        "当前仅支持 PDF、DOCX、Markdown、HTML"
    )


# 删除允许的状态：终态 + uploading（uploading 时后台任务还没真正写 chunks）
_DELETABLE_STATUSES = frozenset(
    {DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.UPLOADING}
)

logger = get_logger(__name__)


class DocumentService:
    def __init__(self, session: AsyncSession, file_service: FileService | None = None) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.chunk_repo = DocumentChunkRepository(session)
        self.file_service = file_service or get_file_service()

    async def upload(self, file: UploadFile, background_tasks: BackgroundTasks) -> Document:
        mime_type, suffix = _resolve_mime_and_suffix(file)
        content = await file.read()
        max_bytes = settings.upload_max_size_mb * 1024 * 1024
        if len(content) == 0:
            raise ValidationError("上传文件为空")
        if len(content) > max_bytes:
            raise ValidationError(f"文件超过 {settings.upload_max_size_mb} MB 上限")
        file_hash = hashlib.sha256(content).hexdigest()
        existing = await self.repo.get_by_hash(file_hash)
        if existing is not None:
            # 命中幂等：直接复用现有记录，不重复入库
            logger.info("file_hash hit, reuse document: %s", existing.id)
            return existing
        object_key = await self.file_service.upload(
            content=content,
            file_hash=file_hash,
            suffix=suffix,
            mime_type=mime_type,
        )
        document = Document(
            name=file.filename or f"{file_hash}{suffix}",
            file_hash=file_hash,
            mime_type=mime_type,
            size=len(content),
            storage_provider="cos",
            cos_bucket=self.file_service.bucket,
            cos_object_key=object_key,
            cos_region=self.file_service.region,
            status=DocumentStatus.UPLOADING,
        )
        await self.repo.add(document)
        await self.session.commit()
        await self.session.refresh(document)
        # 推进到后台任务前 commit，确保 ingest pipeline 用独立 session 也能查到
        background_tasks.add_task(ingest_document, document.id)
        return document

    async def get(self, document_id: UUID) -> Document:
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        return doc

    async def list_documents(
            self,
            page: int,
            page_size: int,
            *,
            status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]:
        return await self.repo.list_paginated(page, page_size, status=status)

    async def delete(self, document_id: UUID) -> None:
        """删除文档。
        DB 是真相之源：先删 DB 行再删 COS object，COS 删除失败仅打 warning，
        避免出现"DB 还在 / 用户以为删了"的更糟状态。
        """
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        if doc.status not in _DELETABLE_STATUSES:
            raise ValidationError("文档处理中，请等待完成或失败后再删除")
        object_key = doc.cos_object_key
        await self.repo.delete(doc)
        await self.session.commit()
        await self.file_service.delete(object_key)
        logger.info("document deleted: id=%s", document_id)

    async def retry(self, document_id: UUID, background_tasks: BackgroundTasks) -> Document:
        """从 failed 重新触发 ingest。"""
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        if doc.status != DocumentStatus.FAILED:
            raise ValidationError("仅失败状态的文档支持重试")
        # 防御性清场：理论上 failed 文档不会有 chunks，但写 chunks 阶段失败时可能残留
        await self.chunk_repo.delete_by_document(document_id)
        doc.status = DocumentStatus.UPLOADING
        doc.error_message = None
        await self.session.commit()
        await self.session.refresh(doc)
        background_tasks.add_task(ingest_document, doc.id)
        logger.info("document retry scheduled: id=%s", document_id)
        return doc

    async def list_chunks(
            self,
            document_id: UUID,
            page: int,
            page_size: int,
    ) -> tuple[list[DocumentChunk], int, ChunkStats | None]:
        # 先确保 document 存在，否则空文档与"文档不存在"会混在一起
        await self.get(document_id)
        items, total = await self.chunk_repo.list_paginated_by_document(
            document_id, page, page_size
        )
        stats = await self.chunk_repo.get_stats(document_id)
        return items, total, stats

    async def get_chunk(self, document_id: UUID, chunk_id: UUID) -> DocumentChunk:
        chunk = await self.chunk_repo.get_for_document(document_id, chunk_id)
        if chunk is None:
            raise NotFoundError("Chunk 不存在")
        return chunk
