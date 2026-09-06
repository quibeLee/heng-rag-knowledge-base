from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from app.core.config import settings
from app.core.exceptions import ConfigurationError
_chat_model: BaseChatModel | None = None
def get_chat_model() -> BaseChatModel:
    """返回流式 ChatOpenAI 实例。
    单例缓存：模型客户端持有 httpx 连接池，反复创建会浪费资源。
    """
    global _chat_model
    if _chat_model is not None:
        return _chat_model
    if not settings.chat_api_key:
        raise ConfigurationError("Chat API key 未配置，请在 .env 设置 CHAT_API_KEY")
    _chat_model = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.chat_api_key,
        base_url=settings.chat_base_url,
        # 知识库问答 + 严格的引用编号约束属于指令遵循任务，温度设 0
        # 避免 [N] 编号在不同 chunk 间漂移
        temperature=0,
        streaming=True,
    )
    return _chat_model