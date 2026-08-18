from __future__ import annotations

import asyncio
import unittest

from app.agents import build_aiops_graph
from app.agents.deep import build_deep_graph
from app.agents.deep.nodes.evidence_plan import evidence_plan_node
from app.agents.deep.nodes.evidence_reducer import evidence_reducer_node
from app.agents.deep.nodes.remediation_planner import remediation_planner_node
from app.agents.deep.nodes.report import report_node
from app.incidents.models import DiagnosisMode
from app.orchestration.diagnosis_runner import make_event, normalize_diagnosis_mode
from app.harness.runtime.tool_runner import partition_tool_calls
from app.schemas.aiops import DiagnosisRequest


class DiagnosisContractTests(unittest.TestCase):
    def test_schema_accepts_existing_mode_aliases(self) -> None:
        aliases = {
            "daily": DiagnosisMode.FAST,
            "normal": DiagnosisMode.FAST,
            "routine": DiagnosisMode.FAST,
            "fast": DiagnosisMode.FAST,
            "deep": DiagnosisMode.DEEP,
            "depth": DiagnosisMode.DEEP,
            "group": DiagnosisMode.DEEP,
        }
        for raw, expected in aliases.items():
            with self.subTest(raw=raw):
                request = DiagnosisRequest(query="x", diagnosis_mode=raw)
                self.assertEqual(request.diagnosis_mode, expected)

    def test_runner_accepts_existing_mode_aliases(self) -> None:
        self.assertEqual(normalize_diagnosis_mode("daily"), DiagnosisMode.FAST)
        self.assertEqual(normalize_diagnosis_mode("group"), DiagnosisMode.DEEP)
        self.assertEqual(normalize_diagnosis_mode("unknown"), DiagnosisMode.FAST)

    def test_event_envelope_is_stable(self) -> None:
        self.assertEqual(
            make_event("report", "report_generated", "done", report="ok"),
            {
                "type": "report",
                "stage": "report_generated",
                "message": "done",
                "data": {"report": "ok"},
            },
        )

    def test_fast_graph_topology_is_stable(self) -> None:
        nodes = set(build_aiops_graph().get_graph().nodes)
        self.assertTrue(
            {"skill_router", "planner", "executor", "replanner"}.issubset(nodes)
        )

    def test_deep_graph_topology_is_stable(self) -> None:
        nodes = set(build_deep_graph().get_graph().nodes)
        self.assertTrue(
            {
                "incident_manager",
                "correlation_context",
                "evidence_plan",
                "log_agent",
                "metric_agent",
                "infra_agent",
                "runbook_agent",
                "evidence_reducer",
                "rca_judge",
                "remediation_planner",
                "report",
            }.issubset(nodes)
        )

    def test_deep_evidence_routing_is_stable(self) -> None:
        self.assertEqual(
            evidence_plan_node({"input": "CPU 负载过高"})["evidence_plan"],
            {"agents": ["metric_agent"], "strategy": "keyword_match"},
        )
        self.assertEqual(
            evidence_plan_node({"input": "全面诊断 Redis 超时"})["evidence_plan"],
            {
                "agents": [
                    "log_agent",
                    "metric_agent",
                    "infra_agent",
                    "runbook_agent",
                ],
                "strategy": "broadcast",
            },
        )

    def test_deep_evidence_reduction_is_stable(self) -> None:
        result = asyncio.run(
            evidence_reducer_node(
                {
                    "evidences": [
                        {
                            "source": "metric",
                            "type": "metric_snapshot",
                            "summary": "CPU 99%",
                            "content": {},
                            "metadata": {"agent": "metric_agent"},
                        },
                        {
                            "source": "log",
                            "type": "log_excerpt",
                            "summary": "timeout",
                            "content": {},
                            "metadata": {"agent": "log_agent"},
                        },
                        {
                            "source": "mcp",
                            "type": "infra_snapshot",
                            "summary": "failed",
                            "content": {"error": "x"},
                            "metadata": {
                                "agent": "infra_agent",
                                "error_type": "RuntimeError",
                            },
                        },
                    ]
                }
            )
        )
        self.assertEqual(
            result["candidates"],
            [
                {
                    "candidate": "CPU 99%",
                    "support_score": 1.0,
                    "evidence_ids": ["ev_0", "ev_2"],
                    "source": "metric",
                    "type": "metric_snapshot",
                    "agent": "metric_agent",
                },
                {
                    "candidate": "timeout",
                    "support_score": 0.85,
                    "evidence_ids": ["ev_1", "ev_2"],
                    "source": "log",
                    "type": "log_excerpt",
                    "agent": "log_agent",
                },
            ],
        )

    def test_deep_remediation_and_report_contract_is_stable(self) -> None:
        remediation = remediation_planner_node(
            {
                "rca": {"root_cause": "Redis 内存占用过高", "via": "fallback"},
                "candidates": [],
            }
        )["remediation"]
        self.assertTrue(remediation["requires_human_confirm"])
        self.assertEqual(remediation["matched_template"], "redis,缓存")

        response = report_node(
            {
                "input": "Redis timeout",
                "rca": {
                    "root_cause": "Redis 内存不足",
                    "confidence": 0.8,
                    "reasoning": "证据支持",
                    "via": "fallback",
                    "supporting_evidence_ids": ["ev_0"],
                },
                "candidates": [],
                "evidences": [],
                "remediation": remediation,
            }
        )["response"]
        self.assertIn("# 深度诊断报告", response)
        self.assertIn("Redis 内存不足", response)
        self.assertIn("需人工确认", response)


class RuntimeContractTests(unittest.TestCase):
    def test_tool_partition_keeps_safe_calls_together_and_unknown_calls_serial(self) -> None:
        batches = partition_tool_calls(
            [
                {"id": "1", "name": "get_current_time", "args": {}},
                {"id": "2", "name": "get_local_cpu_memory", "args": {}},
                {"id": "3", "name": "unknown_tool", "args": {}},
            ],
            max_parallel=4,
        )
        self.assertEqual([safe for safe, _ in batches], [True, False])
        self.assertEqual(
            [call["name"] for call in batches[0][1]],
            ["get_current_time", "get_local_cpu_memory"],
        )
        self.assertEqual(batches[1][1][0]["name"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
