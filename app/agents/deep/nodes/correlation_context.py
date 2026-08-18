"""Build incident-group and historical context for deep diagnosis."""

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.incidents.models import EvidenceSource
from app.harness.runtime.transitions import DEEP_CONTEXT_BUILT, make_transition


async def correlation_context_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """构建同组告警和历史经验上下文。

    IncidentGroup 证据帮助 RCAJudge 判断当前告警是否是更大事件的一部分；
    Wiki recall 是 best-effort 辅助证据，任何召回或数据库失败都不应击穿主图。
    """
    incident_group_id = state.get("incident_group_id") or ""

    # Wiki 经验作为 incident_history 辅证据注入，不替代现场工具证据。
    lessons_evs = []
    try:
        from app.harness.wiki.store import recall_block

        block = await recall_block(
            query=str(state.get("input") or ""),
            signature=str(state.get("alert_signature") or ""),
        )
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
    except Exception as exc:
        logger.warning(
            f"[deep] wiki recall failed (ignored): {type(exc).__name__}: {exc}"
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

    try:
        # 关联查询失败时只记录 transition，EvidenceReducer/RCA 仍可继续。
        from app.incidents.repository import incident_repository

        group = await incident_repository.get_incident_group(incident_group_id)
        if group is None:
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
    except Exception as exc:
        logger.exception(f"[deep] CorrelationContext DB 查询失败: {exc}")
        return {
            "transition_history": [
                make_transition(
                    "correlation_context",
                    DEEP_CONTEXT_BUILT,
                    f"db_error: {type(exc).__name__}",
                )
            ],
        }
