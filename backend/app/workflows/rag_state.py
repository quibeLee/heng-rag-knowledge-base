from typing import TypedDict
from uuid import UUID
from app.db.models import Message
from app.retrieval.vector_retriever import RetrievedChunk
class RAGState(TypedDict, total=False):
    # 输入
    conversation_id: UUID
    question: str
    # load_context 产出
    chat_history: list[Message]
    # normalize_query 产出（本章 = question）
    query: str
    # retrieve 产出
    retrieved_chunks: list[RetrievedChunk]
    # 是否触发拒答（检索不足）。True 时跳过 generate
    refused: bool
    # generate 产出
    answer: str
    # chat_service 落库后回写
    user_message_id: UUID
    assistant_message_id: UUID