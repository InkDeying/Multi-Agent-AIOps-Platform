"""把编排层注入的事件组和历史经验转换为 Deep Evidence。"""

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.evidence.models import EvidenceSource
from app.harness.runtime.transitions import DEEP_CONTEXT_BUILT, make_transition


async def correlation_context_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """构建同组告警和历史经验上下文。

    IncidentGroup 证据帮助 RCAJudge 判断当前告警是否是更大事件的一部分；
    Wiki recall 是 best-effort 辅助证据。节点只消费 state，不执行 IO。
    """
    incident_group_id = state.get("incident_group_id") or ""

    # Wiki 经验作为 incident_history 辅证据注入，不替代现场工具证据。
    lessons_evs = []
    block = str(state.get("wiki_context") or "")
    if block:
        lessons_evs.append(
            {
                "source": str(EvidenceSource.INCIDENT_HISTORY),
                "type": "wiki_recall",
                "summary": ("LLM Wiki 召回: " + " / ".join(block.splitlines()))[
                    :200
                ],
                "content": {"wiki": block},
                "metadata": {
                    "agent": "correlation_context",
                    "kind": "wiki_recall",
                },
            }
        )

    if not incident_group_id:
        # 手动 SSE 没有 group 元信息，仍保留可能成功的 Wiki 召回。
        logger.info("[deep] CorrelationContext: no incident_group_id (manual path)")
        return {
            "evidences": lessons_evs,
            "transition_history": [
                make_transition(
                    "correlation_context",
                    DEEP_CONTEXT_BUILT,
                    "no group (manual path)",
                )
            ],
        }

    status = state.get("incident_group_context_status") or "not_found"
    if status == "error":
        error_type = state.get("incident_group_context_error_type") or "UnknownError"
        logger.warning(f"[deep] CorrelationContext: group context error={error_type}")
        return {
            "transition_history": [
                make_transition(
                    "correlation_context",
                    DEEP_CONTEXT_BUILT,
                    f"db_error: {error_type}",
                )
            ],
        }

    group = state.get("incident_group_context") or {}
    if status != "loaded" or not group:
        logger.warning(
            f"[deep] CorrelationContext: group {incident_group_id} not found"
        )
        return {
            "transition_history": [
                make_transition(
                    "correlation_context",
                    DEEP_CONTEXT_BUILT,
                    f"group {incident_group_id} not found",
                )
            ],
        }

    alert_count = int(group.get("alert_count") or 1)
    primary_service = str(group.get("primary_service") or "")
    severity = str(group.get("severity") or "")
    summary_text = str(group.get("summary") or "")[:200]
    summary = (
        f"本次告警属于 IncidentGroup `{incident_group_id}` "
        f"(共 {alert_count} 条同组告警; service=`{primary_service or '-'}`; "
        f"severity=`{severity or '-'}`)。group summary: {summary_text or '(无)'}"
    )
    evidence = {
        "source": str(EvidenceSource.INCIDENT_HISTORY),
        "type": "incident_history",
        "summary": summary,
        "content": {
            "incident_group_id": incident_group_id,
            "alert_count": alert_count,
            "primary_service": primary_service,
            "severity": severity,
        },
        "metadata": {"agent": "correlation_context"},
    }
    logger.info(
        f"[deep] CorrelationContext: group={incident_group_id} "
        f"alerts={alert_count}"
    )
    return {
        "evidences": [evidence, *lessons_evs],
        "transition_history": [
            make_transition(
                "correlation_context",
                DEEP_CONTEXT_BUILT,
                f"group={incident_group_id} alerts={alert_count} "
                f"svc={primary_service}",
            )
        ],
    }
