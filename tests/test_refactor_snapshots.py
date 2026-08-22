"""重构护栏快照.

这些测试不判断行为"好不好", 只判断行为"有没有变". 它们的存在是为了让
P0-P2 的结构重构可验证: prompt、HTTP 接口面、配置面、工具元数据、
Harness 策略以及 deep 报告渲染器, 在代码搬家前后必须逐字节一致。

依赖环境的开关会被 ``fixed_policy`` 固定住, 这样本地 ``.env`` 不会让快照漂移。
"""

from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

FIXED_POLICY = {
    "max_total_tokens": 100,
    "max_total_ms": 1000,
    "budget_warn_ratio": 0.8,
    "max_agent_steps": 5,
    "max_reroutes": 3,
    "min_reroute_past_steps": 2,
    "replanner_past_step_chars": 200,
}


@contextmanager
def fixed_policy():
    """固定住这些快照依赖的、从 settings 推导出来的 Harness 开关."""
    from app.config import settings
    from app.harness.runtime.agent_harness import AgentHarness

    with ExitStack() as stack:
        for name, value in FIXED_POLICY.items():
            stack.enter_context(
                mock.patch.object(AgentHarness, name, lambda self, v=value: v)
            )
        stack.enter_context(
            mock.patch.object(settings, "agent_replanner_fast_path_threshold", 2)
        )
        yield


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


EXPECTED_ROUTES = [
    "DELETE /api/v1/chat/sessions/{session_id}",
    "DELETE /api/v1/documents/{source}",
    "DELETE /api/v1/incidents/tasks/{task_id}",
    "DELETE /api/v1/webhook/history",
    "GET /api/v1/approvals/pending",
    "GET /api/v1/approvals/recent",
    "GET /api/v1/approvals/{req_id}",
    "GET /api/v1/chat/sessions/{session_id}/history",
    "GET /api/v1/documents",
    "GET /api/v1/eval/reports",
    "GET /api/v1/eval/reports/{name}",
    "GET /api/v1/eval/reports/{name}/low-scores",
    "GET /api/v1/health",
    "GET /api/v1/health/ready",
    "GET /api/v1/incidents/groups/{incident_group_id}",
    "GET /api/v1/incidents/groups/{incident_group_id}/evidence",
    "GET /api/v1/incidents/tasks",
    "GET /api/v1/incidents/tasks/{task_id}",
    "GET /api/v1/incidents/tasks/{task_id}/agent-runs",
    "GET /api/v1/incidents/tasks/{task_id}/evidence",
    "GET /api/v1/incidents/tasks/{task_id}/tool-calls",
    "GET /api/v1/queue/status",
    "GET /api/v1/skills",
    "GET /api/v1/skills/{name}",
    "GET /api/v1/skills/{name}/files",
    "GET /api/v1/webhook/history",
    "GET /api/v1/wiki/index",
    "GET /api/v1/wiki/log",
    "GET /api/v1/wiki/overview",
    "GET /api/v1/wiki/pages",
    "GET /api/v1/wiki/pages/{category}/{slug}",
    "POST /api/v1/aiops/diagnose",
    "POST /api/v1/aiops/diagnose/submit",
    "POST /api/v1/approvals/{req_id}/decide",
    "POST /api/v1/chat/stream",
    "POST /api/v1/documents/upload",
    "POST /api/v1/incidents/from_chat",
    "POST /api/v1/incidents/tasks/bulk-delete",
    "POST /api/v1/skills/reload",
    "POST /api/v1/webhook/alertmanager",
]


class ApiSurfaceSnapshotTests(unittest.TestCase):
    def test_route_inventory_is_stable(self) -> None:
        from app.main import app

        actual = sorted(
            f"{method.upper()} {path}"
            for path, operations in app.openapi()["paths"].items()
            for method in operations
        )
        self.assertEqual(actual, EXPECTED_ROUTES)


class ConfigSurfaceSnapshotTests(unittest.TestCase):
    """守住配置重排: env 名和默认值都不允许变."""

    FIELD_COUNT = 149
    DEFAULTS_DIGEST = "74a1f63082b7a3c9"

    def test_settings_field_inventory_is_stable(self) -> None:
        from app.config import Settings

        names = sorted(Settings.model_fields)
        self.assertEqual(len(names), self.FIELD_COUNT)
        digest = sha(
            "\n".join(f"{n}={Settings.model_fields[n].default!r}" for n in names)
        )
        self.assertEqual(digest, self.DEFAULTS_DIGEST)


class ToolMetaSnapshotTests(unittest.TestCase):
    """守住 P1-5: 风险集合派生自 TOOL_META, 所以 TOOL_META 不能变."""

    DIGEST = "1c94d32707e22034"

    def test_tool_meta_inventory_is_stable(self) -> None:
        from app.harness.tools.meta import TOOL_META

        lines = []
        for name in sorted(TOOL_META):
            m = TOOL_META[name]
            lines.append(
                "|".join(
                    [
                        name,
                        str(m.read_only),
                        str(m.concurrency_safe),
                        str(m.destructive),
                        m.side_effect,
                        m.risk_level,
                        str(m.is_notification),
                        str(m.max_result_chars),
                    ]
                )
            )
        self.assertEqual(sha("\n".join(lines)), self.DIGEST)

    def test_derived_risk_sets_match_tool_meta(self) -> None:
        from app.harness.runtime.tool_filter import HIGH_RISK_TOOLS, NOTIFICATION_TOOLS
        from app.harness.tools.meta import TOOL_META

        self.assertEqual(
            HIGH_RISK_TOOLS,
            {n for n, m in TOOL_META.items() if m.risk_level == "high" or m.destructive},
        )
        self.assertEqual(
            NOTIFICATION_TOOLS,
            {n for n, m in TOOL_META.items() if m.is_notification},
        )


class PromptSnapshotTests(unittest.TestCase):
    """守住 P2-1/P2-3: prompt 文本搬家后必须逐字节不变."""

    FAST_AND_RAG = {
        "skill_router": "226141412f4ac6c3",
        "skill_router_lessons": "fb486411a532af25",
        "planner": "d362cabfb503029c",
        "executor_system": "31467c829eeea2e0",
        "executor_task": "abf914f55eb60b68",
        "replanner": "a8f6b5098b4910a9",
        "report": "41c3023e488f6c33",
        "rag_system_no_tools": "833a434825206825",
        "rag_system_tools": "c8e10270089f3622",
        "rag_user": "f6e6b7f4e2686e60",
        "rag_rewrite": "08aef85b4851885a",
        "rag_compact": "5c626bf5ad5768e9",
    }

    def test_fast_and_rag_prompts_are_stable(self) -> None:
        from app.harness.rag import memory as rag_memory
        from app.harness.runtime.agent_harness import get_agent_harness

        h = get_agent_harness()
        with fixed_policy():
            actual = {
                "skill_router": sha(
                    dumps(
                        h.build_skill_router_messages(
                            menu="MENU", user_input="INPUT", generic="generic_oncall"
                        )
                    )
                ),
                "skill_router_lessons": sha(
                    dumps(
                        h.build_skill_router_messages(
                            menu="MENU",
                            user_input="INPUT",
                            generic="generic_oncall",
                            lessons="LESSON",
                        )
                    )
                ),
                "planner": sha(
                    dumps(
                        h.build_planner_messages(
                            user_input="INPUT",
                            skill_display_name="DISPLAY",
                            skill_playbook="PLAYBOOK",
                        )
                    )
                ),
                "executor_system": sha(h.executor_system_prompt()),
                "executor_task": sha(
                    h.build_executor_task_prompt(
                        plan=["A", "B"], step_index=1, total_steps=2, current_step="A"
                    )
                ),
                "replanner": sha(
                    dumps(
                        h.build_replanner_messages(
                            user_input="INPUT",
                            current_time="T",
                            current_skill_line="SKILL",
                            candidate_skills_text="CANDIDATES",
                            tried_skills_text="TRIED",
                            reroute_count=1,
                            reroute_quota_hint="HINT",
                            plan_text="PLAN",
                            past_steps_text="PAST",
                        )
                    )
                ),
                "report": sha(
                    dumps(
                        h.build_report_messages(
                            user_input="INPUT",
                            past_steps=[("S1", "R1")],
                            current_time="T",
                            draft="DRAFT",
                        )
                    )
                ),
                "rag_system_no_tools": sha(h.rag_system_prompt(tools_enabled=False)),
                "rag_system_tools": sha(h.rag_system_prompt(tools_enabled=True)),
                "rag_user": sha(
                    h.build_rag_user_prompt(
                        summary="S",
                        diagnosis_context="D",
                        context="C",
                        web_context="W",
                        question="Q",
                    )
                ),
                # rewrite/compact 模板随 RAG 能力走 (harness/rag/memory),
                # 模板文本未变, 哈希应与原 facade 版本一致。
                "rag_rewrite": sha(
                    rag_memory.build_rewrite_prompt(summary="S", history="H", question="Q")
                ),
                "rag_compact": sha(
                    rag_memory.build_compact_prompt(
                        max_chars=100, old_summary="S", old_messages="M"
                    )
                ),
            }
        self.assertEqual(actual, self.FAST_AND_RAG)

    DEEP_SPECIALISTS = {
        "log_system": "6f9ce9e5c6c87d51",
        "log_user": "d5988cd2cddd9ccb",
        "log_user_empty": "434779de1091f189",
        "log_evidence": "48f37ab5aa46f1ca",
        "log_evidence_error": "17f98911c62e45d0",
        "metric_system": "14e55df4b468d60d",
        "metric_user": "a65cacbb58322b2c",
        "metric_user_empty": "3a1d97912cea6097",
        "metric_evidence": "54f70e9e2ddb6a2d",
        "metric_evidence_error": "407affd2dfd04e21",
        "infra_system": "970bb19511a169d9",
        "infra_user": "7f82627c16270632",
        "infra_user_empty": "7b5c2d04a364a714",
        "infra_evidence": "944cfeb8a930b0a9",
        "infra_evidence_error": "80fe632318489394",
        "runbook_system": "54d8592752adcd2a",
        "runbook_user": "794ce32627623646",
        "runbook_user_empty": "a23acab4b48d97f9",
        "runbook_evidence": "3e6649a2d06fc664",
        "runbook_evidence_error": "e02030e18c8cca3b",
    }

    def test_deep_specialist_prompts_and_evidence_are_stable(self) -> None:
        """P2-3 已把四个 specialist 收敛到同一个共享 runner.

        它们的 system prompt、user prompt 和 Evidence 结构就是必须在收敛后
        保持不变的契约。取值路径可以随实现调整, 哈希表绝对不要改。
        """
        from app.agents.deep.specialists import get_spec

        actual = {}
        for name in ("log", "metric", "infra", "runbook"):
            spec = get_spec(f"{name}_agent")
            actual[f"{name}_system"] = sha(spec.system_prompt)
            actual[f"{name}_user"] = sha(spec.build_user_prompt("INPUT"))
            actual[f"{name}_user_empty"] = sha(spec.build_user_prompt(""))
            actual[f"{name}_evidence"] = sha(
                dumps(spec.build_evidence("SUM", {"k": 1}, tool_call_count=2))
            )
            actual[f"{name}_evidence_error"] = sha(
                dumps(
                    spec.build_evidence(
                        "SUM", {"k": 1}, tool_call_count=0, error="ValueError"
                    )
                )
            )
        self.assertEqual(actual, self.DEEP_SPECIALISTS)

    def test_specialist_dispatch_contract_matches_registry(self) -> None:
        """图的 fan-out 顺序与 Evidence source/type 只允许有一个声明处."""
        from app.agents.deep.nodes.specialist_dispatch import (
            SPECIALIST_NAMES,
            SPECIALISTS,
        )
        from app.evidence.models import EvidenceSource

        self.assertEqual(
            SPECIALISTS,
            (
                ("log_agent", EvidenceSource.LOG, "log_excerpt"),
                ("metric_agent", EvidenceSource.METRIC, "metric_snapshot"),
                ("infra_agent", EvidenceSource.MCP_TOOL_RESULT, "infra_snapshot"),
                ("runbook_agent", EvidenceSource.RUNBOOK, "runbook_match"),
            ),
        )
        self.assertEqual(
            SPECIALIST_NAMES,
            ("log_agent", "metric_agent", "infra_agent", "runbook_agent"),
        )


class HarnessPolicySnapshotTests(unittest.TestCase):
    """守住 P2-1: 预算、错误分类、replan 策略从 agent_harness 搬出去."""

    def _stats(self, **overrides):
        from app.harness.runtime.agent_harness import HarnessUsageStats

        base = dict(
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            llm_ms=100,
            total_ms=200,
            tool_calls=2,
            tool_ms=50,
            answer_chars=42,
            model="m",
            run_kind="rag_chat",
        )
        base.update(overrides)
        return HarnessUsageStats(**base)

    def test_usage_stats_event_is_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        event = get_agent_harness().build_usage_stats_event(self._stats())
        self.assertEqual(
            event,
            {
                "type": "progress",
                "stage": "stats",
                "label": "本轮统计",
                "detail": (
                    "输入 10 · 输出 20 · 合计 30 tokens · 生成 100ms · "
                    "总耗时 200ms · 工具 2 次"
                ),
                "elapsed_ms": 100,
                "data": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "llm_ms": 100,
                    "total_ms": 200,
                    "tool_calls": 2,
                    "tool_ms": 50,
                    "model": "m",
                    "answer_chars": 42,
                    "run_kind": "rag_chat",
                },
            },
        )

    def test_budget_evaluation_is_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        h = get_agent_harness()
        with fixed_policy():
            ok = h.evaluate_budget(self._stats())
            near = h.evaluate_budget(self._stats(total_tokens=80))
            over = h.evaluate_budget(self._stats(total_tokens=100, total_ms=1000))

        self.assertFalse(ok.exceeded)
        self.assertEqual(ok.warnings, [])
        self.assertIsNone(h.build_budget_event(ok))
        self.assertEqual(
            ok.data,
            {
                "total_tokens": 30,
                "max_total_tokens": 100,
                "token_ratio": 0.3,
                "total_ms": 200,
                "max_total_ms": 1000,
                "time_ratio": 0.2,
                "warn_ratio": 0.8,
                "run_kind": "rag_chat",
            },
        )

        self.assertFalse(near.exceeded)
        self.assertEqual(near.warnings, ["total_tokens_near_limit"])
        near_event = h.build_budget_event(near)
        self.assertEqual(near_event["stage"], "budget_warning")
        self.assertEqual(near_event["label"], "预算接近上限")
        self.assertEqual(near_event["detail"], "total_tokens_near_limit")

        self.assertTrue(over.exceeded)
        self.assertEqual(
            over.warnings, ["total_tokens_exceeded", "total_ms_exceeded"]
        )
        over_event = h.build_budget_event(over)
        self.assertEqual(over_event["stage"], "budget_exceeded")
        self.assertEqual(over_event["label"], "预算已超过")
        self.assertTrue(over_event["data"]["exceeded"])

    def test_error_classification_is_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        classify = get_agent_harness().classify_error
        self.assertEqual(classify(TimeoutError("x")), "transient")
        self.assertEqual(classify(RuntimeError("tool argument invalid")), "llm_recoverable")
        self.assertEqual(classify(RuntimeError("unauthorized 401")), "user_fixable")
        self.assertEqual(classify(RuntimeError("milvus down")), "tool_unavailable")
        self.assertEqual(classify(TypeError("bad")), "code_bug")
        self.assertEqual(classify(RuntimeError("nope")), "unexpected")

    def test_replanner_pre_llm_decisions_are_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        h = get_agent_harness()
        with fixed_policy():
            empty = h.evaluate_replanner_pre_llm({})
            repeated = h.evaluate_replanner_pre_llm(
                {
                    "plan": ["a"],
                    "past_steps": [("查A", "r"), ("查A", "r"), ("查A", "r")],
                    "iteration": 1,
                }
            )
            max_steps = h.evaluate_replanner_pre_llm(
                {"plan": [], "past_steps": [], "iteration": 999}
            )
            fast_path = h.evaluate_replanner_pre_llm(
                {"plan": ["a", "b", "c"], "past_steps": [("s", "ok")], "iteration": 1}
            )
            failed_last = h.evaluate_replanner_pre_llm(
                {
                    "plan": ["a", "b", "c"],
                    "past_steps": [("s", "[执行失败] boom")],
                    "iteration": 1,
                }
            )

        self.assertEqual((empty.action, empty.reason), ("allow_llm", "needs_replanner_llm"))
        self.assertEqual(
            (repeated.action, repeated.reason, repeated.data),
            ("force_report", "repeated_steps_detected", {"repeat_window": 3}),
        )
        self.assertEqual(
            (max_steps.action, max_steps.reason, max_steps.data),
            ("force_report", "max_steps_reached", {"iteration": 999, "max_steps": 5}),
        )
        self.assertEqual(
            (fast_path.action, fast_path.reason, fast_path.data),
            ("continue_fast_path", "fast_path", {"next_plan": ["b", "c"], "remaining": 2}),
        )
        self.assertEqual(failed_last.action, "allow_llm")

    def test_hints_and_past_step_formatting_are_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        h = get_agent_harness()
        with fixed_policy():
            self.assertEqual(h.graph_recursion_limit(), 20)
            self.assertEqual(
                h.build_reroute_quota_hint(reroute_count=99, past_steps_count=5),
                "⚠ reroute 名额已用完, 不允许再切 Skill, 请继续或收尾。",
            )
            self.assertEqual(
                h.build_reroute_quota_hint(reroute_count=0, past_steps_count=0),
                "⚠ 证据不足 (past_steps=0 < 2), 还不允许 reroute。",
            )
            self.assertEqual(
                h.build_reroute_quota_hint(reroute_count=0, past_steps_count=99),
                "可以 reroute (剩余 3 次), 但仅限当前 Skill 方向明确不成立时。",
            )
            self.assertEqual(h.format_past_steps([]), "(暂无已完成的步骤)")
            self.assertEqual(
                h.format_past_steps([("S1", "R1"), ("S2", "R2")]),
                "## 步骤 1: S1\nR1\n\n## 步骤 2: S2\nR2",
            )
        self.assertEqual(
            h.planner_fallback_plan("empty_plan"), ["汇总现有信息, 给出诊断结论"]
        )
        self.assertEqual(
            h.planner_fallback_plan("other"),
            ["查询知识库, 寻找类似问题的处理经验", "汇总现有信息, 给出诊断结论"],
        )

    def test_rag_and_web_fallbacks_are_stable(self) -> None:
        from app.harness.runtime.agent_harness import get_agent_harness

        h = get_agent_harness()
        rag = h.rag_fallback(stage="retrieval", exc=RuntimeError("boom"))
        web = h.web_fallback(stage="web", exc=RuntimeError("boom"))
        self.assertEqual(rag["sources"], [])
        self.assertEqual(rag["hits_meta"], [])
        self.assertEqual(
            rag["event_data"],
            {
                "degraded": True,
                "stage": "retrieval",
                "error_kind": "unexpected",
                "error_type": "RuntimeError",
                "error": "RuntimeError: boom",
                "fallback": "no_rag_context",
            },
        )
        self.assertEqual(web["skip_reason"], "RuntimeError: 联网补充失败")
        self.assertEqual(web["event_data"]["fallback"], "skip_web_context")


class RendererSnapshotTests(unittest.TestCase):
    """守住 P2-5/P2-7/P1-3: 渲染器和文本工具只换文件, 不换输出."""

    DEEP_REPORT = {
        "format_evidence": "7d7f022e89f963d5",
        "format_candidate": "b4b36cb247af72e9",
        "format_remediation_empty": "996e90cd206c34dc",
        "format_remediation": "33249389b0c03d67",
    }

    def test_deep_report_renderers_are_stable(self) -> None:
        from app.agents.deep import report_renderer as report

        actual = {
            "format_evidence": sha(
                report.format_evidence(
                    1,
                    {
                        "source": "log",
                        "type": "log_excerpt",
                        "summary": "S",
                        "metadata": {"agent": "log_agent"},
                    },
                )
            ),
            "format_candidate": sha(
                report.format_candidate(
                    1,
                    {
                        "type": "t",
                        "support_score": 0.5,
                        "agent": "log_agent",
                        "evidence_ids": ["ev_0"],
                        "candidate": "C",
                    },
                )
            ),
            "format_remediation_empty": sha(report.format_remediation({})),
            "format_remediation": sha(
                report.format_remediation(
                    {"steps": ["a", "b"], "requires_human_confirm": True}
                )
            ),
        }
        self.assertEqual(actual, self.DEEP_REPORT)

    def test_wiki_text_helpers_are_stable(self) -> None:
        from app.harness.wiki.text_utils import slug, tokenize

        self.assertEqual(slug("Redis 超时 Alert"), "redis-alert")
        self.assertEqual(
            sorted(tokenize("Redis 超时 timeout 502")),
            ["502", "redis", "timeout", "时", "超"],
        )

    def test_content_to_text_is_stable(self) -> None:
        from app.harness.core.llm_parse import content_to_text

        self.assertEqual(content_to_text("abc"), "abc")
        self.assertEqual(
            content_to_text(
                [{"type": "text", "text": "a"}, {"type": "other"}, {"type": "text", "text": "b"}]
            ),
            "ab",
        )


class RagMemoryCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewrite_without_context_returns_original_without_llm(self) -> None:
        from app.harness.rag import memory as rag_memory

        with mock.patch.object(rag_memory, "get_chat_llm") as get_llm:
            result = await rag_memory.rewrite_question(
                "Redis 超时怎么办",
                summary="",
                recent_messages=[],
            )

        self.assertEqual(result, "Redis 超时怎么办")
        get_llm.assert_not_called()

    async def test_rewrite_uses_harness_prompt_and_normalizes_content(self) -> None:
        from app.harness.rag import memory as rag_memory

        llm = mock.Mock()
        llm.ainvoke = mock.AsyncMock(
            return_value=SimpleNamespace(
                content=[{"type": "text", "text": '"Redis 连接池耗尽"'}]
            )
        )

        with mock.patch.object(rag_memory, "get_chat_llm", return_value=llm):
            result = await rag_memory.rewrite_question(
                "这个怎么办",
                summary="Redis 连接异常",
                recent_messages=[{"role": "user", "content": "Redis 连接池耗尽"}],
            )

        self.assertEqual(result, "Redis 连接池耗尽")
        llm.ainvoke.assert_awaited_once()

    async def test_summarize_history_returns_bounded_summary(self) -> None:
        from app.harness.rag import memory as rag_memory

        llm = mock.Mock()
        llm.ainvoke = mock.AsyncMock(
            return_value=SimpleNamespace(content="Redis 连接池耗尽, 需要检查 maxclients")
        )

        with mock.patch.object(rag_memory, "get_chat_llm", return_value=llm):
            result = await rag_memory.summarize_history(
                max_chars=11,
                old_summary="已有摘要",
                old_messages=[{"role": "user", "content": "检查 Redis"}],
            )

        self.assertEqual(result, "Redis 连接池耗尽")
        llm.ainvoke.assert_awaited_once()

    async def test_summarize_history_failure_returns_none(self) -> None:
        from app.harness.rag import memory as rag_memory

        with mock.patch.object(
            rag_memory,
            "get_chat_llm",
            side_effect=RuntimeError("llm unavailable"),
        ):
            result = await rag_memory.summarize_history(
                max_chars=100,
                old_summary="",
                old_messages=[{"role": "user", "content": "检查 Redis"}],
            )

        self.assertIsNone(result)


class RagSessionMemoryOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_only_persists_after_summary_is_ready(self) -> None:
        from app.services.rag import memory as session_memory

        messages = [
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "保留问题"},
            {"role": "assistant", "content": "保留回答"},
        ]

        with (
            mock.patch.object(session_memory.settings, "rag_chat_memory_enabled", True),
            mock.patch.object(session_memory.settings, "rag_chat_compact_enabled", True),
            mock.patch.object(session_memory.settings, "rag_chat_max_messages", 3),
            mock.patch.object(session_memory.settings, "rag_chat_compact_keep_messages", 2),
            mock.patch.object(session_memory.settings, "rag_chat_summary_max_chars", 100),
            mock.patch.object(
                session_memory.chat_memory,
                "get_messages",
                new=mock.AsyncMock(return_value=messages),
            ),
            mock.patch.object(
                session_memory.chat_memory,
                "get_summary",
                new=mock.AsyncMock(return_value="旧摘要"),
            ),
            mock.patch.object(
                session_memory,
                "summarize_history",
                new=mock.AsyncMock(return_value="新摘要"),
            ) as summarize,
            mock.patch.object(
                session_memory.chat_memory,
                "set_summary",
                new=mock.AsyncMock(),
            ) as set_summary,
            mock.patch.object(
                session_memory.chat_memory,
                "replace_messages",
                new=mock.AsyncMock(),
            ) as replace_messages,
        ):
            await session_memory.compact_if_needed("session-1")

        summarize.assert_awaited_once_with(
            max_chars=100,
            old_summary="旧摘要",
            old_messages=messages[:-2],
        )
        set_summary.assert_awaited_once_with("session-1", "新摘要")
        replace_messages.assert_awaited_once_with("session-1", messages[-2:])


if __name__ == "__main__":
    unittest.main()
