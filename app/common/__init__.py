"""跨层纯工具的规范入口.

这里只允许放无业务语义、无 IO、无 settings 依赖的纯函数。
带有 RAG、Incident、数据库、HTTP 或 Agent 语义的工具必须留在所属层级。
"""
