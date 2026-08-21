"""四个专业 Agent 的工具装载.

全部延迟导入: deep graph 装配阶段不应该因为 Prometheus / MCP / RAG 还没就绪就失败,
也不应该在构图时就拉起 langchain ``@tool`` 子树。
"""

from __future__ import annotations

from typing import Any, List

# infra Agent 允许使用的 MCP 只读工具名单。写操作 (docker_restart 等) 明确不在内。
MCP_INFRA_TOOL_NAMES = {
    "docker_ps",
    "docker_stats",
    "docker_logs",
    "docker_inspect",
    "dns_lookup",
    "http_check",
    "check_port",
}


def load_knowledge_tools() -> List[Any]:
    """log / runbook 共用: 只有知识库检索一个工具, 靠 scoped prompt 区分职责."""
    from app.harness.tools.knowledge_tool import search_knowledge_base

    return [search_knowledge_base]


def load_metric_tools() -> List[Any]:
    """Prometheus (真后端) 优先, 本机 system 工具兜底.

    Prom 未配置时 ``get_prom_tools()`` 返回 [], 自动退化为纯本机集合。
    Prom 工具排在前: LLM 看到工具列表会优先尝试真指标, 失败再走本机。
    """
    from app.harness.tools.prom_tool import get_prom_tools
    from app.harness.tools.system_tool import (
        get_local_cpu_memory,
        get_local_disk_usage,
        get_local_system_overview,
        list_top_processes,
    )

    prom_tools = get_prom_tools()
    local_tools = [
        get_local_system_overview,
        get_local_cpu_memory,
        get_local_disk_usage,
        list_top_processes,
    ]
    return [*prom_tools, *local_tools]


def load_infra_tools() -> List[Any]:
    """本地 system 工具永远可用; Docker / Network 工具来自 MCP, 已连接时加入."""
    from app.harness.mcp.client import mcp_client_manager
    from app.harness.tools.system_tool import (
        get_local_disk_usage,
        get_local_system_overview,
        list_top_processes,
    )

    tools: List[Any] = [
        get_local_system_overview,
        get_local_disk_usage,
        list_top_processes,
    ]
    seen = {tool.name for tool in tools if getattr(tool, "name", "")}

    for tool in mcp_client_manager.tools:
        name = getattr(tool, "name", "")
        if name in MCP_INFRA_TOOL_NAMES and name not in seen:
            tools.append(tool)
            seen.add(name)
    return tools
