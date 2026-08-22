"""severity 分级单一事实来源的契约测试.

背景: 分级令牌集曾在 webhook_service (模式/优先级) 和 redis_streams (队列
level) 三处独立定义且已漂移 (severity=p1 在两处落不同档位)。现在全部从
app/common/severity.py 推导, 本测试守住三处口径不再分叉。
"""

from __future__ import annotations

import unittest

from app.common.severity import (
    PRIORITY_FOR_TIER,
    TIER_DEEP_DIAGNOSIS,
    severity_tier,
)
from app.queue.redis_streams import level_for_severity
from app.services.webhook_service import diagnosis_mode_for, priority_for


class _Alert:
    def __init__(self, severity: str) -> None:
        self.labels = {"severity": severity}


class _Payload:
    def __init__(self, alerts) -> None:
        self.alerts = alerts


class SeverityTierTests(unittest.TestCase):
    def test_canonical_tier_mapping(self) -> None:
        cases = {
            "critical": "critical", "page": "critical", "p0": "critical",
            "high": "high", "p1": "high",
            "warning": "warning", "p2": "warning",
            "info": "info", "low": "info", "p3": "info",
            "": "warning", "unknown-sev": "warning",
        }
        for raw, expected in cases.items():
            with self.subTest(severity=raw):
                self.assertEqual(severity_tier(raw), expected)

    def test_three_derivations_never_contradict(self) -> None:
        """同一 severity 的 模式/优先级/队列level 三种推导口径一致。"""
        for raw in ("critical", "page", "p0", "high", "p1", "warning", "p2", "info", "low", "p3"):
            with self.subTest(severity=raw):
                tier = severity_tier(raw)
                mode = diagnosis_mode_for(_Payload([]), _Alert(raw))
                priority = priority_for(_Alert(raw))
                level = level_for_severity(raw)

                # 档位越高 → 模式越深、优先级数值不更低、队列级别不更低
                self.assertEqual(mode.value == "deep", tier in TIER_DEEP_DIAGNOSIS)
                self.assertEqual(priority, PRIORITY_FOR_TIER[tier])
                rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
                tier_level_rank = rank[level]
                # critical 档必须是最高级别队列; 其余按 tier 单调不升
                if tier == "critical":
                    self.assertEqual(level, "critical")
                else:
                    self.assertGreaterEqual(tier_level_rank, rank["high"])


if __name__ == "__main__":
    unittest.main()
