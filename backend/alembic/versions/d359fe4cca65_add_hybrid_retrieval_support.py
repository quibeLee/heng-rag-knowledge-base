"""add hybrid retrieval support

Revision ID: d359fe4cca65
Revises: 19efa1807881
Create Date: 2026-09-06 20:14:23.067911

"""
from typing import Sequence, Union
import sqlalchemy as sa
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd359fe4cca65'
down_revision: Union[str, Sequence[str], None] = '19efa1807881'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # zhparser 扩展由自定义 postgres.Dockerfile 安装，这里建扩展 + 文本搜索配置
    op.execute("CREATE EXTENSION IF NOT EXISTS zhparser")
    # 自定义文本搜索配置 chinese_zh：使用 zhparser 切词，仅保留检索意义大的词性，
    # 过滤掉助词 / 标点 / 量词等噪音 token，避免索引体积膨胀。
    # n=名词 v=动词 a=形容词 i=习用语 e=叹词 l=习惯用语
    op.execute(
        "CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser)"
    )
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION chinese_zh "
        "ADD MAPPING FOR n,v,a,i,e,l WITH simple"
    )
    # GENERATED ALWAYS：tsvector 由 DB 根据 content 自动维护，
    # 应用代码插入 chunk 时不需要也不应当显式赋值，避免漏同步导致索引脏数据。
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('chinese_zh', content)) STORED"
    )
    # GIN 索引：tsvector 全文检索的标配；@@ 操作符走它做倒排查找
    op.execute(
        "CREATE INDEX ix_document_chunks_content_tsv "
        "ON document_chunks USING GIN (content_tsv)"
    )

    # 混合检索调试元数据落库：sources / vector_rank / keyword_rank / *_score / rrf_score
    # 选 JSONB 而不是拆列，因为后续 reranker 还会继续往里加字段，schema 不稳定时
    # JSONB 一次到位；这些字段也不参与 SQL 过滤 / 聚合，没必要走列存
    op.add_column(
        "answer_citations",
        sa.Column("retrieval_meta", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("answer_citations", "retrieval_meta")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese_zh")
    op.execute("DROP EXTENSION IF EXISTS zhparser")
