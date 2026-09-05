import hashlib
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.config import settings


def _build_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        # 中文场景下默认分隔符过于偏英文，这里加入中文标点；空串作为兜底
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
        is_separator_regex=False,
    )


def split(documents: list[Document]) -> list[Document]:
    """切分并补齐 chunk 级 metadata。"""
    splitter = _build_splitter()
    chunks = splitter.split_documents(documents)
    for index, chunk in enumerate(chunks):
        # chunk_index 用于排序与定位；chunk_hash 给后续增量索引比对
        chunk.metadata["chunk_index"] = index
        chunk.metadata["chunk_hash"] = hashlib.md5(
            chunk.page_content.encode("utf-8")
        ).hexdigest()
    return chunks
