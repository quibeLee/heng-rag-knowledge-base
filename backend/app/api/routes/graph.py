from fastapi import APIRouter

from app.api.schemas.graph import GraphMermaidRead
from app.workflows.graph import get_rag_graph

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get(
    "/graph/mermaid",
    response_model=GraphMermaidRead,
    operation_id="getAgentGraphMermaid",
)
async def get_agent_graph_mermaid() -> GraphMermaidRead:
    """输出 Agentic RAG 图结构的 Mermaid 文本，用于直观分析流程。"""
    mermaid = get_rag_graph().get_graph().draw_mermaid()
    return GraphMermaidRead(mermaid=mermaid)
