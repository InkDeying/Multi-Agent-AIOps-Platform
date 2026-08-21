"""一次 Agent 运行的用量统计与预算判定.

从 ``runtime/agent_harness.py`` 搬出来的原因: 这几个函数只跟"数字和阈值"有关,
和 prompt、模型档位、Skill 都无关, 混在 826 行的 Harness 类里没有理由。

这里的函数都是纯函数 —— 阈值由调用方 (``AgentHarness``) 从 settings 取好传进来,
本模块不读 settings, 便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class HarnessUsageStats:
    """一次运行的 token / 耗时 / 工具调用统计."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_ms: int = 0
    total_ms: int = 0
    tool_calls: int = 0
    tool_ms: int = 0
    answer_chars: int = 0
    model: str = ""
    run_kind: str = ""


@dataclass(frozen=True)
class HarnessBudgetStatus:
    """预算判定结果: 是否超限 + 告警项 + 给前端的明细."""

    exceeded: bool
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def evaluate_budget(
    stats: HarnessUsageStats,
    *,
    token_limit: int,
    time_limit: int,
    warn_ratio: float,
) -> HarnessBudgetStatus:
    """按 token / 耗时两个维度判定预算.

    限额为 0 表示该维度不限制; 统计值为 0 时同样跳过 (还没产生用量)。
    """
    warnings: list[str] = []
    exceeded = False

    if token_limit > 0 and stats.total_tokens > 0:
        token_ratio = stats.total_tokens / token_limit
        if stats.total_tokens >= token_limit:
            exceeded = True
            warnings.append("total_tokens_exceeded")
        elif token_ratio >= warn_ratio:
            warnings.append("total_tokens_near_limit")
    else:
        token_ratio = 0.0

    if time_limit > 0 and stats.total_ms > 0:
        time_ratio = stats.total_ms / time_limit
        if stats.total_ms >= time_limit:
            exceeded = True
            warnings.append("total_ms_exceeded")
        elif time_ratio >= warn_ratio:
            warnings.append("total_ms_near_limit")
    else:
        time_ratio = 0.0

    return HarnessBudgetStatus(
        exceeded=exceeded,
        warnings=warnings,
        data={
            "total_tokens": stats.total_tokens,
            "max_total_tokens": token_limit,
            "token_ratio": round(token_ratio, 4),
            "total_ms": stats.total_ms,
            "max_total_ms": time_limit,
            "time_ratio": round(time_ratio, 4),
            "warn_ratio": warn_ratio,
            "run_kind": stats.run_kind,
        },
    )


def build_usage_stats_event(stats: HarnessUsageStats) -> dict[str, Any]:
    """渲染成 SSE progress 事件 (前端"本轮统计"那一行)."""
    detail_parts = []
    if stats.input_tokens or stats.output_tokens or stats.total_tokens:
        detail_parts.append(
            f"输入 {stats.input_tokens} · 输出 {stats.output_tokens} · 合计 {stats.total_tokens} tokens"
        )
    if stats.llm_ms:
        detail_parts.append(f"生成 {stats.llm_ms}ms")
    detail_parts.append(f"总耗时 {stats.total_ms}ms")
    if stats.tool_calls:
        detail_parts.append(f"工具 {stats.tool_calls} 次")
    return {
        "type": "progress",
        "stage": "stats",
        "label": "本轮统计",
        "detail": " · ".join(detail_parts),
        "elapsed_ms": stats.llm_ms,
        "data": {
            "input_tokens": stats.input_tokens,
            "output_tokens": stats.output_tokens,
            "total_tokens": stats.total_tokens,
            "llm_ms": stats.llm_ms,
            "total_ms": stats.total_ms,
            "tool_calls": stats.tool_calls,
            "tool_ms": stats.tool_ms,
            "model": stats.model,
            "answer_chars": stats.answer_chars,
            "run_kind": stats.run_kind,
        },
    }


def build_budget_event(status: HarnessBudgetStatus) -> dict[str, Any] | None:
    """预算正常时返回 None (不发事件), 接近或超限时返回 SSE progress 事件."""
    if not status.exceeded and not status.warnings:
        return None
    stage = "budget_exceeded" if status.exceeded else "budget_warning"
    label = "预算已超过" if status.exceeded else "预算接近上限"
    return {
        "type": "progress",
        "stage": stage,
        "label": label,
        "detail": ", ".join(status.warnings),
        "elapsed_ms": status.data.get("total_ms", 0),
        "data": status.data | {
            "exceeded": status.exceeded,
            "warnings": status.warnings,
        },
    }
