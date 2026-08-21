"""异常归类: 把任意 exception 归到有限几种"处置方式"上.

从 ``runtime/agent_harness.py`` 搬出来。归类结果决定上层是重试、让 LLM 自己修、
提示用户改配置, 还是直接当 bug 抛出去, 所以它是一份策略表而不是日志格式化。

判定顺序有意义: 先认瞬时错误 (可重试), 最后才落到 ``unexpected``。
"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal[
    "transient",
    "llm_recoverable",
    "user_fixable",
    "tool_unavailable",
    "code_bug",
    "unexpected",
]


def classify_error(exc: BaseException) -> ErrorKind:
    """按异常类型名 + 文本特征归类."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text or "temporar" in text or "connection reset" in text:
        return "transient"
    if "tool" in text and ("argument" in text or "schema" in text or "validation" in text):
        return "llm_recoverable"
    if "api key" in text or "unauthorized" in text or "401" in text or "permission" in text:
        return "user_fixable"
    if "mcp" in text or "milvus" in text or "connection refused" in text:
        return "tool_unavailable"
    if name in {
        "typeerror",
        "attributeerror",
        "importerror",
        "modulenotfounderror",
        "nameerror",
        "keyerror",
    }:
        return "code_bug"
    return "unexpected"
