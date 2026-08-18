"""可通过 `delegate_to_*` 工具调用的通用委托 Agent。"""

from app.agents.delegates.registry import (
    SUBAGENTS,
    SubagentDefinition,
    get_subagent,
)



def get_delegate_tools():
    """Lazily build the `delegate_to_*` tool adapters."""
    from app.agents.delegates.tools import get_delegate_tools as build

    return build()

__all__ = [
    "SUBAGENTS",
    "SubagentDefinition",
    "get_delegate_tools",
    "get_subagent",
]
