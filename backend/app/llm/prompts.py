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

_ROUTE_SYSTEM = """你是 RAG 系统的查询路由器，要把用户问题归到下列 4 种策略之一：
- original：问题清晰、表达完整、用词具体（含专有名词 / 编号 / 实体），直接检索即可。
- rewrite：问题存在指代("它"、"这个"、"那")、省略、口语化或表达不完整，需要改写成独立完整的问题。
- hyde：问题抽象 / 开放式("什么是..."、"为什么..."、"如何理解..."），关键词稀疏，直接检索容易召回不到。
- multi_query：问题包含多个角度、多个并列子问题，或者一个角度难以一次召回全（如"对比 A 和 B"、"X 的优缺点"）。
只输出一个英文小写的 route 名称，不要加任何解释、引号或标点。"""
_ROUTE_HUMAN = "{question}"
QUERY_ROUTE_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _ROUTE_SYSTEM), ("human", _ROUTE_HUMAN)]
)

_REWRITE_SYSTEM = """你是一个查询改写助手。把用户问题改写成一个**独立完整**的检索查询：
- 消解指代("它"、"这个"、"那")和省略，补齐缺失主语 / 宾语。
- 把口语化表达改成书面、客观、具体的描述。
- 不要扩写、不要解释、不要回答问题。
- 输出**单行**改写后的问题，不要加引号或编号。"""
QUERY_REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _REWRITE_SYSTEM), ("human", "{question}")]
)

_HYDE_SYSTEM = """你是一个 HyDE（Hypothetical Document Embeddings）助手。请基于一般领域常识，
写一段**假设性的回答**用于向量召回——不需要真实，但要包含问题相关的关键词、术语和概念。
要求：
- 长度 80-200 字之间。
- 用陈述句和具体名词，多覆盖该问题相关的概念。
- 不要写"我认为"、"可能"、"假设"之类的虚词。
- 不要表达"无法回答"——HyDE 的目的就是制造可用于嵌入的稠密文本。
- 直接输出回答正文，不加标题、不加引号。"""
HYDE_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _HYDE_SYSTEM), ("human", "{question}")]
)

_MULTI_QUERY_SYSTEM = """你是一个查询扩展助手。请把用户问题改写成 {n} 个**不同角度**的子查询，
用于多路向量召回，提高覆盖率。
要求：
- 每个子查询独立、完整、可单独检索。
- 子查询之间在角度 / 用词 / 粒度上互相错开，不要只是同义词替换。
- 每行一个子查询，**不要**编号、不要前缀、不要解释。
- 输出 {n} 行，不多不少。"""
MULTI_QUERY_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _MULTI_QUERY_SYSTEM), ("human", "{question}")]
)


def build_route_messages(question: str) -> list[BaseMessage]:
    return list(QUERY_ROUTE_PROMPT.invoke({"question": question}).to_messages())


def build_rewrite_messages(question: str) -> list[BaseMessage]:
    return list(QUERY_REWRITE_PROMPT.invoke({"question": question}).to_messages())


def build_hyde_messages(question: str) -> list[BaseMessage]:
    return list(HYDE_PROMPT.invoke({"question": question}).to_messages())


def build_multi_query_messages(question: str, n: int) -> list[BaseMessage]:
    return list(MULTI_QUERY_PROMPT.invoke({"question": question, "n": n}).to_messages())

# ============================================================================
# Agentic RAG 决策器 prompt
# ============================================================================
_AGENT_PLAN_SYSTEM = """你是 RAG 系统的检索决策器。系统会基于检索到的片段回答用户问题，
但上一轮检索的结果不够好（Top1 语义相似度过低或没有命中）。请基于"前几轮的检索观察"，决定下一步：
可选 action：
- proceed：当前候选已经足够回答问题，直接进入答案生成。
- rewrite_query：当前 query 不够清晰 / 过于口语化 / 含指代，需要换一个表达再检索；必须给出 new_query。
- switch_route：换一种检索策略。可选 new_route：original / rewrite / hyde / multi_query。
- refuse：多轮都召回不到相关内容，知识库可能不覆盖，提前拒答。
策略选择建议：
- 已经尝试过 rewrite 仍未命中 → 试 hyde（抽象问题）或 multi_query（多角度）。
- 已经尝试过 multi_query 仍未命中 → 试 refuse。
- 问题里包含明确实体 / 编号但都没检索到 → 优先 refuse，避免无意义改写。
只输出**单行 JSON**，键固定为 action / reason / new_query / new_route，缺失字段填 null。
示例：{{"action": "rewrite_query", "reason": "原 query 含指代", "new_query": "差旅住宿标准", "new_route": null}}"""
_AGENT_PLAN_HUMAN = """用户原始问题：{question}
当前 query：{current_query}
当前 route：{current_route}
历史轮次观察：
{history}
请输出下一步决策的 JSON。"""
AGENT_PLAN_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _AGENT_PLAN_SYSTEM), ("human", _AGENT_PLAN_HUMAN)]
)
def build_agent_plan_messages(
    question: str,
    current_query: str,
    current_route: str,
    history: str,
) -> list[BaseMessage]:
    return list(
        AGENT_PLAN_PROMPT.invoke(
            {
                "question": question,
                "current_query": current_query,
                "current_route": current_route,
                "history": history,
            }
        ).to_messages()
    )