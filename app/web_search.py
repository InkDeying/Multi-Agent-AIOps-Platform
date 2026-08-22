"""Backward-compatible re-export.

真实实现已迁至 ``app.harness.websearch``。
保留本文件是因为 ``mcp_servers/websearch_server.py`` 是独立进程,
通过 sys.path 操作直接 import app.web_search;
主应用内部请直接依赖 ``app.harness.websearch``。
"""

from app.harness.websearch import (  # noqa: F401
    format_results,
    get_provider,
    search,
)

__all__ = [
    "format_results",
    "get_provider",
    "search",
]
