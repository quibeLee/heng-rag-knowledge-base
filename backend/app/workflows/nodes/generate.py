from collections.abc import AsyncIterator
from app.llm.models import get_chat_model
from app.llm.prompts import build_answer_messages
from app.workflows.rag_state import RAGState
async def stream_generate(state: RAGState) -> AsyncIterator[str]:
    """流式生成：逐 token yield。
    调用方负责拼接成完整答案并写回 state；这里只关心"逐块输出"。
    refused 状态下调用方应直接跳过本函数。
    """
    messages = build_answer_messages(
        question=state["question"],
        chunks=state["retrieved_chunks"],
        history=state.get("chat_history", []),
    )
    async for chunk in get_chat_model().astream(messages):
        text = chunk.content
        if not text:
            continue
        # langchain 的 content 类型是 str | list[str | dict]，本章只用纯文本模型，
        # 理论上一直走 str 分支；list 分支兜底是为了过 pyright 的类型检查。
        if isinstance(text, str):
            yield text
        else:
            yield "".join(part.get("text", "") for part in text if isinstance(part, dict))