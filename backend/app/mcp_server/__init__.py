"""MCP Server 入口模块。

把知识库的核心能力（问答 / 文档上传 / 列表 / 状态查询 / 统计）按 MCP 标准
工具协议暴露给外部 Agent。鉴权复用第 11 章的 JWT，工具实现一律调
`app.services.*` 与 `app.workflows.*`，不重写业务逻辑。
"""

from app.mcp_server.server import knowledge_mcp

__all__ = ["knowledge_mcp"]
