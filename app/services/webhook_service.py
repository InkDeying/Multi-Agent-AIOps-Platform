"""Alertmanager Webhook 业务编排.

API 层只负责请求模型、限流和 HTTP 响应；告警归一化、入账和入队由本服务完成。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.common.severity import (
    PRIORITY_FOR_TIER,
    TIER_DEEP_DIAGNOSIS,
    severity_tier,
)
from app.incidents.dispatch import dispatch_diagnosis_task
from app.incidents.models import DiagnosisMode
from app.incidents.repository import incident_repository


def format_alert_as_query(alert: Any) -> str:
    """把结构化告警渲染成诊断 query 文本."""
    name = alert.labels.get("alertname", "UnknownAlert")
    severity = alert.labels.get("severity", "warning")
    instance = alert.labels.get("instance", "")
    service = alert.labels.get("service", "")
    summary = alert.annotations.get("summary", "")
    description = alert.annotations.get("description", "")
    runbook = alert.annotations.get("runbook_url", "")

    parts = [
        f"[{str(severity).upper()}] {name} alert firing",
        f"instance: {instance or '(unknown)'}",
    ]
    if service:
        parts.append(f"service: {service}")
    if summary:
        parts.append(f"summary: {summary}")
    if description:
        parts.append(f"description: {description}")
    if alert.startsAt:
        parts.append(f"startsAt: {alert.startsAt}")
    if runbook:
        parts.append(f"runbook: {runbook}")
    parts.append(
        "Act as an OnCall SRE. Diagnose likely root cause and provide remediation advice."
    )
    return "\n".join(parts)


def diagnosis_mode_for(payload: Any, alert: Any) -> DiagnosisMode:
    """按严重等级或告警数量选择 fast/deep; 分级口径见 app/common/severity.py."""
    tier = severity_tier(str(alert.labels.get("severity", "")))
    if tier in TIER_DEEP_DIAGNOSIS:
        return DiagnosisMode.DEEP
    if len(payload.alerts) >= 10:
        return DiagnosisMode.DEEP
    return DiagnosisMode.FAST


def priority_for(alert: Any) -> int:
    """按严重等级映射队列优先级; 分级口径见 app/common/severity.py."""
    tier = severity_tier(str(alert.labels.get("severity", "")))
    return PRIORITY_FOR_TIER[tier]


async def process_alertmanager_payload(payload: Any) -> dict[str, Any]:
    """完成一批 Alertmanager 告警的入账与入队."""
    accepted: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []

    for idx, alert in enumerate(payload.alerts):
        alertname = str(alert.labels.get("alertname", f"alert_{idx}"))
        instance = str(alert.labels.get("instance", "unknown"))
        if alert.status != "firing":
            skipped.append(alertname)
            continue

        query = format_alert_as_query(alert)
        diagnosis_mode = diagnosis_mode_for(payload, alert)
        priority = priority_for(alert)

        try:
            result = await incident_repository.ingest_alertmanager_alert(
                payload=payload,
                alert=alert,
                query=query,
                diagnosis_mode=diagnosis_mode,
                priority=priority,
            )
        except Exception as exc:
            # 只有入库失败才算这条告警 failed; 入队失败在下面单独处理。
            logger.exception(
                f"[webhook] alert={alertname} ingestion failed: {exc}"
            )
            failed.append(
                {
                    "alertname": alertname,
                    "instance": instance,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        # 入库成功后的投递: needs_enqueue 覆盖 "新任务" 和 "复用 pending 幽灵任务"。
        # 投递失败只降级 enqueued=False, 任务事实已在 Postgres, 由 Worker 补偿重投。
        queue_message_id = ""
        enqueued = False
        if result.needs_enqueue:
            try:
                queue_message_id = await dispatch_diagnosis_task(
                    task_id=result.task_id,
                    incident_group_id=result.incident_group_id,
                    incident_id=result.incident_id,
                    diagnosis_mode=diagnosis_mode.value,
                    priority=priority,
                    payload={
                        "query": query,
                        "alert_id": result.alert_id,
                        "alertname": alertname,
                        "severity": alert.labels.get("severity", ""),
                        "instance": instance,
                        "summary": alert.annotations.get("summary", ""),
                        "fingerprint": alert.fingerprint or "",
                        "startsAt": alert.startsAt,
                    },
                ) or ""
            except Exception as exc:
                # dispatch 内部已处理 XADD 失败; 这里兜底 claim 阶段的 DB 异常。
                logger.exception(
                    f"[webhook] task={result.task_id} dispatch error: {exc}"
                )
            enqueued = bool(queue_message_id)
            if not enqueued:
                logger.warning(
                    f"[webhook] task={result.task_id} not enqueued now; "
                    "worker reconciler will retry"
                )

        accepted.append(
            {
                "alertname": alertname,
                "incident_group_id": result.incident_group_id,
                "incident_id": result.incident_id,
                "task_id": result.task_id,
                "task_created": result.task_created,
                "enqueued": enqueued,
                "queue_message_id": queue_message_id,
                "diagnosis_mode": diagnosis_mode.value,
            }
        )

    logger.info(
        f"[webhook] received={len(payload.alerts)} "
        f"accepted={len(accepted)} skipped={len(skipped)} failed={len(failed)}"
    )
    return {
        "status": "accepted",
        "received": len(payload.alerts),
        "accepted": accepted,
        "skipped": skipped,
        "failed": failed,
    }
