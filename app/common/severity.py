"""告警 severity 的分级口径 (单一事实来源).

此前 "哪些 token 算哪一档" 在 webhook_service (模式选择 / 优先级) 和
redis_streams (队列 level) 各写一份且已经漂移 (同一 severity=p1 在两处
落在不同档位, 导致 DB priority 与队列 level 互相矛盾)。
所有分级口径从本模块推导 —— 属于跨层复用的纯函数, 不读 settings 不做 IO。
"""

from __future__ import annotations

CRITICAL_TOKENS = frozenset({"critical", "page", "p0"})
HIGH_TOKENS = frozenset({"high", "p1"})
WARNING_TOKENS = frozenset({"warning", "p2"})
INFO_TOKENS = frozenset({"info", "low", "p3"})

# 诊断模式选择: critical / high 走 deep (多 Agent 深度取证)
TIER_DEEP_DIAGNOSIS = frozenset({"critical", "high"})

# 入库 priority (数值越小越优先)
PRIORITY_FOR_TIER: dict[str, int] = {
    "critical": 10,
    "high": 50,
    "warning": 50,
    "info": 100,
}

# 队列 stream level (分级流控)
LEVEL_FOR_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "warning": "normal",
    "info": "low",
}


def severity_tier(severity: str) -> str:
    """归一化到 critical / high / warning / info; 未知值归 warning (保守)。"""
    token = str(severity or "").lower().strip()
    if token in CRITICAL_TOKENS:
        return "critical"
    if token in HIGH_TOKENS:
        return "high"
    if token in INFO_TOKENS:
        return "info"
    return "warning"
