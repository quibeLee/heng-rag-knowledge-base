from dataclasses import dataclass

from collections.abc import Sequence
from uuid import UUID
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload
from app.db.models import DocumentChunk, Document
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy import and_, or_

# 通配权限标签：admin 角色持有，含义"无视权限过滤"
WILDCARD_PERMISSION_TAG = "*"


@dataclass(frozen=True)
class ChunkStats:
    """单个文档下的 chunk 长度统计。
    全部 None 表示该文档当前没有任何 chunk（未入库 / 入库失败）。
    """
    total: int
    avg_length: int
    min_length: int
    max_length: int


def _permission_where(permission_tags: list[str] | None) -> ColumnElement[bool] | None:
    """构造文档可见性 WHERE 条件。
    - None：调用方（评测 / 启动期种子）显式不限制
    - 含 "*"：admin 通配，不加条件
    - 其他：'空权限标签视为公开' OR '数组重叠'
    返回 None 表示不附加任何额外 WHERE；非 None 时由调用方 .where() 拼上。
    """
    if permission_tags is None:
        return None
    if WILDCARD_PERMISSION_TAG in permission_tags:
        return None
    return or_(
        func.cardinality(Document.permission_tags) == 0,
        Document.permission_tags.op("&&")(permission_tags),
    )


class DocumentChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_add(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
        self.session.add_all(chunks)
        await self.session.flush()

    async def delete_by_document(self, document_id: UUID) -> None:
        stmt = delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        await self.session.execute(stmt)

    async def list_paginated_by_document(
            self,
            document_id: UUID,
            page: int,
            page_size: int,
    ) -> tuple[list[DocumentChunk], int]:
        offset = (page - 1) * page_size
        items_stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .offset(offset)
            .limit(page_size)
        )
        count_stmt = (
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
        )
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), int(total)

    async def get_for_document(
            self, document_id: UUID, chunk_id: UUID
    ) -> DocumentChunk | None:
        """按 document_id + chunk_id 双条件查询。
        强校验归属，避免拿 A 文档的 id 越权读 B 文档的 chunk。
        """
        stmt = select(DocumentChunk).where(
            DocumentChunk.id == chunk_id,
            DocumentChunk.document_id == document_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_stats(self, document_id: UUID) -> ChunkStats | None:
        """一条聚合 SQL 拿到 count/avg/min/max，避免在 Python 侧再扫一遍 chunks。"""
        length = func.char_length(DocumentChunk.content)
        stmt = select(
            func.count().label("total"),
            func.avg(length).label("avg_len"),
            func.min(length).label("min_len"),
            func.max(length).label("max_len"),
        ).where(DocumentChunk.document_id == document_id)
        row = (await self.session.execute(stmt)).one()
        if not row.total:
            return None
        return ChunkStats(
            total=int(row.total),
            avg_length=int(row.avg_len or 0),
            min_length=int(row.min_len or 0),
            max_length=int(row.max_len or 0),
        )

    async def vector_search(
            self,
            query_embedding: list[float],
            top_k: int,
            *,
            permission_tags: list[str] | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """按 cosine 距离做 Top-K 向量检索。
        - 仅检索状态为 ready 的文档（避免拿到尚未完成入库的脏 chunk）
        - 返回 (chunk, distance) 列表，distance 越小越相似（pgvector cosine_distance）
        - 用 selectinload 把所属 Document 一并加载，方便上层直接读 document.name
          而不会再发 N 次 lazy load 查询
        """
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        conditions: list[ColumnElement[bool]] = [
                    Document.status == "ready",
        ]
        perm_where = _permission_where(permission_tags)
        if perm_where is not None:
            conditions.append(perm_where)
        stmt = (
            select(DocumentChunk, distance.label("distance"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(and_(*conditions))
            .order_by(distance.asc())
            .limit(top_k)
            .options(selectinload(DocumentChunk.document))
        )
        rows = (await self.session.execute(stmt)).all()
        return [(chunk, float(dist)) for chunk, dist in rows]

    async def keyword_search(
            self,
            query: str,
            top_k: int,
            *,
            permission_tags: list[str] | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """中文全文检索 Top-K：plainto_tsquery + ts_rank。
        - 用 chinese_zh 文本搜索配置（zhparser 切词，迁移里建好）
        - plainto_tsquery：自动把多个词 AND 起来，对用户输入容错最好
          （"差旅 报销"和"差旅报销"都会切成同一组 token）
        - 仅命中 status='ready' 文档，避免拿到尚未完成入库的脏 chunk
        - 返回 (chunk, ts_rank) 列表，ts_rank 越大越相关
        """
        tsquery = func.plainto_tsquery("chinese_zh", query)
        rank_expr = func.ts_rank(DocumentChunk.content_tsv, tsquery)
        conditions: list[ColumnElement[bool]] = [
                    Document.status == "ready",
                    DocumentChunk.content_tsv.op("@@")(tsquery),
        ]
        perm_where = _permission_where(permission_tags)

        if perm_where is not None:
             conditions.append(perm_where)
        stmt = (
            select(DocumentChunk, rank_expr.label("rank"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(and_(*conditions))
            .order_by(rank_expr.desc())
            .limit(top_k)
            .options(selectinload(DocumentChunk.document))
        )
        rows = (await self.session.execute(stmt)).all()
        return [(chunk, float(rank)) for chunk, rank in rows]
