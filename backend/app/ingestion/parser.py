import asyncio
import io
from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from langchain_core.documents import Document
from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)


class DocumentParseError(AppException):
    code = "document_parse_error"
    message = "文档解析失败"
    http_status = 400


_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    """单例：DocumentConverter 内部会加载模型，初始化较重。"""
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter


def _convert_sync(filename: str, content: bytes) -> str:
    source = DocumentStream(name=filename, stream=io.BytesIO(content))
    result = _get_converter().convert(source)
    return result.document.export_to_markdown()


async def parse(filename: str, content: bytes) -> list[Document]:
    """解析并转成 LangChain Document 列表。"""
    try:
        markdown = await asyncio.to_thread(_convert_sync, filename, content)
    except Exception as exc:
        logger.exception("docling parse failed: %s", filename)
        raise DocumentParseError(f"docling 解析失败：{exc}") from exc
    if not markdown.strip():
        raise DocumentParseError("解析结果为空，文档可能损坏或不受支持")
    return [
        Document(
            page_content=markdown,
            metadata={"source": filename},
        )
    ]
