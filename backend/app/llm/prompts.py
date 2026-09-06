_SYSTEM_PROMPT = """你是企业知识库助手，必须严格遵守以下规则：
1. 只基于下面【参考资料】中提供的【片段】作答，禁止使用片段之外的常识或主观推断。
2. 如果所有片段都无法回答用户问题，直接回复："抱歉，知识库中没有找到相关信息。" 不要编造。
3. 回答使用简体中文，使用 Markdown 排版（必要时使用列表、加粗等结构）。
4. 引用规则（**最重要**，违反任何一条都视为错误）：
   - 在每个结论后用方括号标注片段编号，例如 [1] 或 [2][3]。
   - 编号 N 必须**精确指向"下方编号为 N 的那个片段"**，并且该结论的内容能在 N 号片段的原文中**直接找到对应文字**。
   - **禁止**因为某个片段与结论"同属一份文档"就标该片段编号；同一份文档的不同片段算不同片段。
   - **禁止**把多个编号合写成 [1, 2] 或 [1-3]，多个并列写成 [1][2]。
   - **禁止**在编号外加反引号或尖括号，如 `[1]`、<1>。
   - 找不到能直接支撑该结论的片段，就**不要给那句话加引用**，宁缺毋滥。
5. 不要重复粘贴参考资料原文，只引用其中关键信息。
【正确示例】
片段 1：差旅住宿标准为一线城市每晚不超过 600 元。
片段 2：差旅日均餐补为 100 元。
回答："住宿标准为一线城市每晚不超过 600 元 [1]，餐补每日 100 元 [2]。"
【错误示例】（同一份文档不同片段，不可串用）
片段 1：差旅住宿标准为一线城市每晚不超过 600 元。
片段 2：差旅日均餐补为 100 元。
回答："餐补每日 100 元 [1]。"  ← 错：餐补信息出自片段 2，不是片段 1。
【参考资料】
{context}
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
RAG_ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{question}"),
    ]
)

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from app.db.models import Message, MessageRole
from app.retrieval.vector_retriever import RetrievedChunk
def format_context(chunks: list[RetrievedChunk]) -> str:
    """把检索结果拼成给 LLM 的【参考资料】文本。
    用「片段 N」而非「来源：xxx」做强标记，避免 LLM 把 [N] 误解为
    "第 N 份文档"——同一文档命中多 chunk 时这种误解会导致引用张冠李戴。
    """
    if not chunks:
        return "（无）"
    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        meta = f"来自《{chunk.document_name}》"
        if chunk.page_no is not None:
            meta += f"，第 {chunk.page_no} 页"
        if chunk.section_path:
            meta += f"，章节：{chunk.section_path}"
        parts.append(f"【片段 {index}】（{meta}）\n{chunk.content}")
    return "\n\n---\n\n".join(parts)
def history_to_messages(history: list[Message]) -> list[BaseMessage]:
    """把数据库 Message 转成 langchain BaseMessage，用于塞进 prompt。"""
    messages: list[BaseMessage] = []
    for msg in history:
        if msg.role == MessageRole.USER:
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == MessageRole.ASSISTANT:
            messages.append(AIMessage(content=msg.content))
        elif msg.role == MessageRole.SYSTEM:
            messages.append(SystemMessage(content=msg.content))
    return messages
def build_answer_messages(
    question: str,
    chunks: list[RetrievedChunk],
    history: list[Message],
) -> list[BaseMessage]:
    """组装最终送给 LLM 的 messages 列表。"""
    prompt_value = RAG_ANSWER_PROMPT.invoke(
        {
            "context": format_context(chunks),
            "question": question,
            "chat_history": history_to_messages(history),
        }
    )
    return list(prompt_value.to_messages())
# 检索失败时的固定拒答文案，集中管理便于后续章节统一调整
REFUSAL_ANSWER = "抱歉，知识库中没有找到与该问题相关的可靠依据。"