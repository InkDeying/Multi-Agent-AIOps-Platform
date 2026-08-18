"""基础工具加载器.

负责把"本地 @tool 工具"和"MCP 远程工具"合并成基础工具列表。
Agent 可见的完整工具目录由 :mod:`app.harness.tools.catalog` 统一装配。

设计要点:
  - 本地工具是同步加载 (装饰器自动注册)
  - MCP 工具是异步加载 (在 lifespan 启动时已 connect, 这里直接读已加载列表)
  - 本模块不依赖 Agent 或 delegate 实现
"""

from typing import List

from langchain_core.tools import BaseTool
from loguru import logger

from app.harness.mcp.client import mcp_client_manager
from app.harness.tools.knowledge_tool import search_knowledge_base
from app.harness.tools.system_tool import (
    get_local_cpu_memory,
    get_local_disk_usage,
    get_local_system_overview,
    list_top_processes,
)
from app.harness.tools.time_tool import get_current_time


def get_local_tools() -> List[BaseTool]:
    """返回所有本地 @tool 工具.

    注: web_search 仍由 MCP server (mcp_servers/websearch_server.py) 提供.
    本机系统诊断工具 (get_local_*) 同时提供本地实现和 MCP 实现, 本地优先
    (见 get_base_tools 的同名去重逻辑), 目的是让 Agent 在 MCP system_server
    没跑起来时仍能诊断本机.
    """
    return [
        search_knowledge_base,
        get_current_time,
        get_local_system_overview,
        get_local_cpu_memory,
        get_local_disk_usage,
        list_top_processes,
    ]


def get_base_tools() -> List[BaseTool]:
    """返回本地工具 + 已加载的 MCP 工具。

    注意: 必须在 mcp_client_manager.connect() 完成之后调用, 否则只能拿到本地工具.
    通常在 Agent 节点构造时调用一次, 然后缓存.

    Returns:
        去重后的基础工具列表。
    """
    local = get_local_tools()
    mcp = mcp_client_manager.tools

    # 同名去重: 本地 > MCP (本地实现永远可用, 不依赖 MCP 进程)
    # LangChain create_agent 不允许同名工具, 这里提前合并.
    seen: set[str] = set()
    base_tools: list[BaseTool] = []
    mcp_skipped = 0
    for t in list(local) + list(mcp):
        if t.name in seen:
            mcp_skipped += 1
            continue
        seen.add(t.name)
        base_tools.append(t)
    if mcp_skipped:
        logger.debug(f"基础工具集合: 跳过 {mcp_skipped} 个 MCP 重名工具 (本地优先)")

    logger.info(
        f"基础工具集合: 本地={len(local)} + MCP={len(mcp)} "
        f"-> 去重后 {len(base_tools)} 个"
    )
    for t in base_tools:
        logger.debug(f"  tool: {t.name} - {(t.description or '')[:60]}")
    return base_tools
