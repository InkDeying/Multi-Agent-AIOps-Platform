"""MCP 工具服务 (独立进程).

每个文件是一个独立的 MCP server, 通过 streamable-http transport 暴露工具.
被 multi_agent 主应用通过 langchain_mcp_adapters 远程调用.

启动 (或用 scripts/run_all.* / docker compose app profile 托管):
  python mcp_servers/system_server.py       # 端口 9105 (本机 psutil)
  python mcp_servers/websearch_server.py    # 端口 9106 (联网搜索)
  python mcp_servers/winlog_server.py       # 端口 9108 (Windows 事件日志)
  python mcp_servers/network_server.py      # 端口 9109 (网络诊断)
  python mcp_servers/docker_server.py       # 端口 9111 (Docker 管理)
"""
