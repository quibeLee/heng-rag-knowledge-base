from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Document, DocumentStatus


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, document_id: UUID) -> Document | None:
        return await self.session.get(Document, document_id)

    async def get_by_hash(self, file_hash: str) -> Document | None:
        stmt = select(Document).where(Document.file_hash == file_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def update_status(
            self,
            document_id: UUID,
            status: DocumentStatus,
            *,
            error_message: str | None = None,
    ) -> None:
        doc = await self.get_by_id(document_id)
        if doc is None:
            return
        doc.status = status
        # 仅在显式传入时覆盖；保留 None 语义供成功状态清空之前的错误信息
        if error_message is not None or status != DocumentStatus.FAILED:
            doc.error_message = error_message

    async def list_paginated(
            self,
            page: int,
            page_size: int,
            *,
            status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]:
        offset = (page - 1) * page_size
        items_stmt = (
            select(Document)
            .order_by(Document.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        count_stmt = select(func.count()).select_from(Document)
        if status is not None:
            items_stmt = items_stmt.where(Document.status == status)
            count_stmt = count_stmt.where(Document.status == status)
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), int(total)

    async def delete(self, document: Document) -> None:
        """删除文档。chunks 走 ORM 级联删除（Document.chunks 配了 delete-orphan）。"""
        await self.session.delete(document)
