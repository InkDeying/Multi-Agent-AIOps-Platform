"""Alertmanager Webhook 业务编排.

API 层只负责请求模型、限流和 HTTP 响应；告警归一化、入账和入队由本服务完成。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.incidents.models import DiagnosisMode
from app.incidents.repository import incident_repository
from app.queue.redis_streams import incident_queue, level_for_severity


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
    """按严重等级或告警数量选择 fast/deep."""
    severity = str(alert.labels.get("severity", "")).lower()
    if severity in {"critical", "page", "p0", "p1"}:
        return DiagnosisMode.DEEP
    if len(payload.alerts) >= 10:
        return DiagnosisMode.DEEP
    return DiagnosisMode.FAST


def priority_for(alert: Any) -> int:
    """按严重等级映射队列优先级."""
    severity = str(alert.labels.get("severity", "")).lower()
    if severity in {"critical", "page", "p0"}:
        return 10
    if severity in {"warning", "p1", "p2"}:
        return 50
    return 100


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

            queue_message_id = ""
            enqueued = False
            if result.task_created:
                queue_message_id = await incident_queue.enqueue_task(
                    task_id=result.task_id,
                    incident_group_id=result.incident_group_id,
                    incident_id=result.incident_id,
                    diagnosis_mode=diagnosis_mode.value,
                    priority=priority,
                    level=level_for_severity(
                        str(alert.labels.get("severity", ""))
                    ),
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
                )
                await incident_repository.set_task_queue_message(
                    result.task_id,
                    queue_message_id,
                )
                enqueued = True

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
        except Exception as exc:
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
