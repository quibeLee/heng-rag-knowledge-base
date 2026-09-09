import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
from typing import Sequence
from uuid import UUID
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk, DocumentStatus, IngestionTask, IngestionTaskType
from app.db.repositories.chunk_repo import (
    ChunkStats,
    DocumentChunkRepository,
)
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.ingestion_task_repo import IngestionTaskRepository
from app.ingestion.tasks import ingest_document_task, reindex_document_task
from app.storage.file_service import FileService, get_file_service

logger = get_logger(__name__)

# 受支持的 MIME 类型。Docling 还支持其他格式，目前开放常见六种
_ACCEPTED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
}
_ACCEPTED_SUFFIXES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        "当前仅支持 PDF、DOCX、PPTX、XLSX、Markdown、HTML"
    )


# 删除允许的状态：终态 + uploading（uploading 时后台任务还没真正写 chunks）
_DELETABLE_STATUSES = frozenset(
    {DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.UPLOADING}
)


def _normalize_tags(tags: Sequence[str] | None) -> list[str]:
    """去空白、去空串、去重；None 视为空数组。"""
    if not tags:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        t = tag.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        result.append(t)
    return result



@dataclass(frozen=True)
class KnowledgeBaseStats:
    """知识库整体规模快照，按调用者权限范围统计。
    chunk_count 仅计入 status='ready' 的文档，与检索可见性一致；
    last_indexed_at 取最近一次 ready 文档的 updated_at（含 reindex 后的刷新）。
    """
    document_count: int
    chunk_count: int
    last_indexed_at: datetime | None

class DocumentService:
    def __init__(self, session: AsyncSession, file_service: FileService | None = None) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.chunk_repo = DocumentChunkRepository(session)
        self.ingestion_task_repo = IngestionTaskRepository(session)
        self.file_service = file_service or get_file_service()

    async def upload(
            self,
            file: UploadFile,
            *,
            created_by: UUID | None = None,
            permission_tags: list[str] | None = None,
    ) -> Document:
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
            permission_tags=_normalize_tags(permission_tags),
            created_by=created_by,
        )
        await self.repo.add(document)
        task = await self.ingestion_task_repo.create(document.id, IngestionTaskType.INGEST)
        await self.session.commit()
        await self.session.refresh(document)
        # commit 之后 Celery worker 用独立 session 才能查到刚落库的 document / task
        ingest_document_task.delay(str(document.id), str(task.id))
        return document

    async def get(
            self, document_id: UUID, *,
            permission_tags: list[str] | None = None,
    ) -> Document:
        doc = await self.repo.get_by_id(document_id, permission_tags=permission_tags)
        if doc is None:
            raise NotFoundError("文档不存在")
        return doc

    async def list_documents(
            self, page: int, page_size: int, *,
            status: DocumentStatus | None = None,
            permission_tags: list[str] | None = None,
    ) -> tuple[list[Document], int]:
        return await self.repo.list_paginated(
            page, page_size, status=status, permission_tags=permission_tags,
        )

    async def get_latest_task(self, document_id: UUID) -> IngestionTask | None:
        """列表/详情页「最近一次任务」进度卡片用。"""
        return await self.ingestion_task_repo.get_latest_by_document(document_id)

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

    async def retry(self, document_id: UUID) -> Document:
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
        task = await self.ingestion_task_repo.create(doc.id, IngestionTaskType.INGEST)
        await self.session.commit()
        await self.session.refresh(doc)
        ingest_document_task.delay(str(doc.id), str(task.id))
        logger.info("document retry scheduled: id=%s", document_id)
        return doc

    async def list_chunks(
            self, document_id: UUID, page: int, page_size: int, *,
            permission_tags: list[str] | None = None,
    ) -> tuple[list[DocumentChunk], int, ChunkStats | None]:
        # 先确保 document 存在 + 当前用户可见
        await self.get(document_id, permission_tags=permission_tags)
        items, total = await self.chunk_repo.list_paginated_by_document(
            document_id, page, page_size
        )
        stats = await self.chunk_repo.get_stats(document_id)
        return items, total, stats

    async def get_chunk(
            self,
            document_id: UUID,
            chunk_id: UUID,
            *,
            permission_tags: list[str] | None = None,
    ) -> DocumentChunk:
        # 双重校验：先确保用户能看到 document，再校验 chunk 归属
        await self.get(document_id, permission_tags=permission_tags)
        chunk = await self.chunk_repo.get_for_document(document_id, chunk_id)
        if chunk is None:
            raise NotFoundError("Chunk 不存在")
        return chunk

    async def update_permission_tags(
            self, document_id: UUID, tags: Sequence[str]
    ) -> Document:
        """admin 修改文档可见性标签。"""
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        doc.permission_tags = _normalize_tags(tags)
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def reindex(
            self,
            document_id: UUID,
            file: UploadFile,
    ) -> Document:
        """用新文件替换原文档并触发增量重建。
        - 只允许 READY / FAILED 状态触发，避免与正在进行的 ingest 抢资源
        - 文件 MIME 必须与原文档一致：避免「PDF 文档被 Markdown 覆盖」造成的
          预览 / 下载链路状态混乱
        - 新文件覆盖到 COS 的同一个 object_key，version+1 由 worker 在 reindex
          成功后才提交，避免失败的话用户列表里看到版本号但内容没变
        """
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        if doc.status not in {DocumentStatus.READY, DocumentStatus.FAILED}:
            raise ValidationError("文档处理中，请等待完成或失败后再重新索引")
        mime_type, suffix = _resolve_mime_and_suffix(file)
        if mime_type != doc.mime_type:
            raise ValidationError(
                f"新版本文件类型必须与原文档一致（当前为 {doc.mime_type}）"
            )
        content = await file.read()
        max_bytes = settings.upload_max_size_mb * 1024 * 1024
        if len(content) == 0:
            raise ValidationError("上传文件为空")
        if len(content) > max_bytes:
            raise ValidationError(f"文件超过 {settings.upload_max_size_mb} MB 上限")
        new_hash = hashlib.sha256(content).hexdigest()
        if new_hash == doc.file_hash:
            # 内容完全一致没有重建必要，避免学员误操作浪费 embedding 配额
            raise ValidationError("文件内容与现有版本一致，无需重新索引")
        new_object_key = await self.file_service.upload(
            content=content,
            file_hash=new_hash,
            suffix=suffix,
            mime_type=mime_type,
        )
        doc.file_hash = new_hash
        doc.size = len(content)
        doc.cos_object_key = new_object_key
        doc.cos_bucket = self.file_service.bucket
        doc.cos_region = self.file_service.region
        doc.status = DocumentStatus.PARSING
        doc.error_message = None
        if file.filename:
            doc.name = file.filename
        task = await self.ingestion_task_repo.create(doc.id, IngestionTaskType.REINDEX)
        await self.session.commit()
        await self.session.refresh(doc)
        reindex_document_task.delay(str(doc.id), str(task.id))
        logger.info("document reindex scheduled: id=%s", document_id)
        return doc

    async def get_stats(
            self,
            *,
            permission_tags: list[str] | None = None,
    ) -> KnowledgeBaseStats:
        """聚合 documents / chunks 总数与最新入库时间。
        permission_tags：admin 视角传 None 不限；普通用户传合并后的有效标签，
        三处统计 SQL 共用 `_permission_where` 保证可见性一致。
        """
        document_count = await self.repo.count(permission_tags=permission_tags)
        chunk_count = await self.chunk_repo.count_visible(
            permission_tags=permission_tags
        )
        last_indexed_at = await self.repo.get_last_indexed_at(
            permission_tags=permission_tags
        )
        return KnowledgeBaseStats(
            document_count=document_count,
            chunk_count=chunk_count,
            last_indexed_at=last_indexed_at,
        )
