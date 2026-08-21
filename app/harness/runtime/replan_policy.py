"""Replanner 的"不调 LLM 就能定的事" —— 调 LLM 之前的三条硬规则.

从 ``runtime/agent_harness.py`` 搬出来。这一层存在的意义是省钱和防死循环:
步数用尽、检测到原地打转、或者计划里还剩足够多的步骤时, 都不必再问一次 LLM。

纯函数, 阈值由调用方从 settings 取好传入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

HarnessAction = Literal["allow_llm", "continue_fast_path", "force_report"]


@dataclass(frozen=True)
class HarnessDecision:
    """一次 pre-LLM 判定: 做什么 + 为什么 + 附带数据."""

    action: HarnessAction
    reason: str
    data: dict[str, Any] = field(default_factory=dict)


def fingerprint_step(text: str) -> str:
    """步骤文本指纹: 只留字母数字和汉字, 用于判断"是不是同一步"."""
    return "".join(
        ch.lower() for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )[:100]


def last_step_failed(past_steps: list[Any]) -> bool:
    """最后一步是否以执行失败/超步数收尾 (失败后不走 fast path)."""
    if not past_steps:
        return False
    try:
        result = past_steps[-1][1]
    except Exception:
        return False
    head = str(result or "")[:80]
    return head.startswith("[执行失败") or head.startswith("[超过最大步数")


def has_repeated_steps(past_steps: list[Any]) -> bool:
    """最近 3 步是否是同一个步骤 (原地打转, 强制收尾)."""
    if len(past_steps) < 3:
        return False
    fingerprints = []
    for item in past_steps[-3:]:
        try:
            step = item[0]
        except Exception:
            return False
        fingerprint = fingerprint_step(str(step))
        if not fingerprint:
            return False
        fingerprints.append(fingerprint)
    return len(set(fingerprints)) == 1


def evaluate_pre_llm(
    state: Mapping[str, Any],
    *,
    max_steps: int,
    fast_path_threshold: int,
) -> HarnessDecision:
    """调 Replanner LLM 之前的判定.

    Args:
        state: fast 图的 state (读 plan / past_steps / iteration)。
        max_steps: 单次诊断步数上限。
        fast_path_threshold: 计划剩余步数达到多少时直接照计划走 (0 = 关闭)。
    """
    plan = list(state.get("plan") or [])
    past_steps = list(state.get("past_steps") or [])
    iteration = int(state.get("iteration") or 0)

    if iteration >= max_steps:
        return HarnessDecision(
            action="force_report",
            reason="max_steps_reached",
            data={"iteration": iteration, "max_steps": max_steps},
        )

    if has_repeated_steps(past_steps):
        return HarnessDecision(
            action="force_report",
            reason="repeated_steps_detected",
            data={"repeat_window": 3},
        )

    plan_remaining = max(0, len(plan) - 1)
    if (
        fast_path_threshold > 0
        and plan_remaining >= fast_path_threshold
        and not last_step_failed(past_steps)
        and iteration < max_steps - 1
    ):
        next_plan = list(plan[1:])
        return HarnessDecision(
            action="continue_fast_path",
            reason="fast_path",
            data={"next_plan": next_plan, "remaining": len(next_plan)},
        )

    return HarnessDecision(action="allow_llm", reason="needs_replanner_llm")
