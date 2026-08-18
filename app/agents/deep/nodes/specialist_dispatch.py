"""专业 Agent 注册、延迟解析和 EvidencePlan dispatch guard。

这里保存的是图的专业节点契约：节点名、Evidence source 和 Evidence type。
真实 Agent 只在首次执行时导入，避免 graph 构建阶段拉起完整工具/LLM 依赖。
"""

import asyncio
from typing import Any, Callable

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.incidents.models import EvidenceSource
from app.harness.runtime.transitions import DEEP_AGENT_DONE, make_transition


SPECIALISTS = (
    ("log_agent", EvidenceSource.LOG, "log_excerpt"),
    ("metric_agent", EvidenceSource.METRIC, "metric_snapshot"),
    ("infra_agent", EvidenceSource.MCP_TOOL_RESULT, "infra_snapshot"),
    ("runbook_agent", EvidenceSource.RUNBOOK, "runbook_match"),
)
# EvidencePlan 使用同一顺序生成 broadcast 计划；图边顺序也由 SPECIALISTS 决定。
SPECIALIST_NAMES = tuple(name for name, _, _ in SPECIALISTS)


def _stub_evidence(
    source: EvidenceSource, evidence_type: str, summary: str
) -> dict[str, Any]:
    """为未来尚未实现的 specialist 提供与 EvidenceCreate 对齐的占位结果。"""
    return {
        "source": str(source),
        "type": evidence_type,
        "summary": summary,
        "content": {"stub": True},
        "score": None,
    }


def _make_specialist_node(
    name: str, source: EvidenceSource, evidence_type: str
) -> Callable:
    """创建安全降级节点，避免新增 specialist 破坏整张图的编译。"""
    def node(state: DeepDiagnosisState) -> DeepDiagnosisState:
        logger.info(f"[deep] {name} (stub) -> {evidence_type}")
        evidence = _stub_evidence(
            source, evidence_type, f"（stub）{name} 取证未实现"
        )
        return {
            "evidences": [evidence],
            "transition_history": [
                make_transition(name, DEEP_AGENT_DONE, f"stub: {evidence_type}")
            ],
        }

    return node


def _dispatch_guard(name: str, inner: Callable) -> Callable:
    """跳过未被 EvidencePlan 派遣的 Agent，避免无意义的 LLM 调用。

    fan-out 仍然保持固定拓扑；未命中的节点只写一条 skipped transition，
    不生成 Evidence，后续 Reducer/RCA/Report 负责处理证据不足场景。
    """
    async def async_wrapper(state: DeepDiagnosisState) -> DeepDiagnosisState:
        plan = state.get("evidence_plan") or {}
        agents = plan.get("agents") or list(SPECIALIST_NAMES)
        if name not in agents:
            logger.info(
                f"[deep] {name} skipped (not in evidence_plan.agents={agents})"
            )
            return {
                "transition_history": [
                    make_transition(
                        name,
                        DEEP_AGENT_DONE,
                        f"skipped (plan.strategy={plan.get('strategy', '-')})",
                    )
                ],
            }
        if asyncio.iscoroutinefunction(inner):
            return await inner(state)
        return inner(state)

    return async_wrapper


def resolve_specialist_node(
    name: str, source: EvidenceSource, evidence_type: str
) -> Callable:
    """按节点名延迟解析真实 Agent，未知节点回退到 stub。"""
    # 延迟 import 是有意的：构图不应依赖 Prometheus、MCP 或 RAG 工具已就绪。
    if name == "metric_agent":
        from app.agents.deep.nodes.metric_agent import run_metric_agent

        inner = run_metric_agent
    elif name == "log_agent":
        from app.agents.deep.nodes.log_agent import run_log_agent

        inner = run_log_agent
    elif name == "infra_agent":
        from app.agents.deep.nodes.infra_agent import run_infra_agent

        inner = run_infra_agent
    elif name == "runbook_agent":
        from app.agents.deep.nodes.runbook_agent import run_runbook_agent

        inner = run_runbook_agent
    else:
        inner = _make_specialist_node(name, source, evidence_type)
    return _dispatch_guard(name, inner)
