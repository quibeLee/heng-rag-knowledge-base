from pydantic import BaseModel


class GraphMermaidRead(BaseModel):
    """LangGraph 图结构的 Mermaid 文本，前端用于渲染流程图。"""

    mermaid: str
