"""Agent 可见工具目录的统一装配入口。"""

from __future__ import annotations

from typing import List

from langchain_core.tools import BaseTool
from loguru import logger

from app.harness.tools.loader import get_base_tools


def get_all_tools() -> List[BaseTool]:
    """合并基础工具与 delegate 工具，并按名称去重。"""
    from app.agents.delegates.tools import get_delegate_tools
    from app.harness.tools.meta import warn_unregistered_tools

    base_tools = get_base_tools()
    delegate_tools = get_delegate_tools()

    seen: set[str] = set()
    all_tools: list[BaseTool] = []
    skipped = 0
    for tool in [*base_tools, *delegate_tools]:
        if tool.name in seen:
            skipped += 1
            continue
        seen.add(tool.name)
        all_tools.append(tool)

    if skipped:
        logger.debug(f"工具目录: 跳过 {skipped} 个重名工具 (基础工具优先)")
    logger.info(
        f"工具目录: 基础={len(base_tools)} + Delegate={len(delegate_tools)} "
        f"-> 去重后 {len(all_tools)} 个"
    )

    # 未登记工具按保守默认处理；这里集中检查最终对 Agent 可见的目录。
    warn_unregistered_tools([tool.name for tool in all_tools])
    return all_tools
