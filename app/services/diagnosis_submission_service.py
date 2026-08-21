"""手动诊断排队用例服务."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.incidents.models import DiagnosisMode
from app.incidents.repository import incident_repository
from app.queue.redis_streams import incident_queue, level_for_severity


async def submit(
    *,
    query: str,
    mode: str,
    session_id: str,
    severity: str,
    service: str,
) -> dict[str, Any]:
    try:
        diagnosis_mode = DiagnosisMode(mode.lower().strip())
    except Exception:
        diagnosis_mode = DiagnosisMode.FAST

    result = await incident_repository.create_manual_task(
        source=f"submit:{session_id}",
        title=query[:80],
        query=query,
        severity=severity or "warning",
        service=service or "",
        diagnosis_mode=diagnosis_mode,
        context={"session_id": session_id, "entry": "diagnose_submit"},
    )

    enqueued = ""
    if settings.incident_pipeline_enabled and result.task_created:
        try:
            enqueued = await incident_queue.enqueue_task(
                task_id=result.task_id,
                incident_group_id=result.incident_group_id,
                incident_id=result.incident_id,
                diagnosis_mode=diagnosis_mode.value,
                priority=100,
                level=level_for_severity(severity),
                payload={
                    "query": query,
                    "alertname": query[:80],
                    "severity": severity,
                    "service": service,
                    "source": f"submit:{session_id}",
                },
            )
            await incident_repository.set_task_queue_message(
                result.task_id,
                enqueued,
            )
        except Exception:
            enqueued = ""

    position = await incident_repository.queue_position(result.task_id)
    return {
        "task_id": result.task_id,
        "incident_group_id": result.incident_group_id,
        "status": "queued" if result.task_created else "running",
        "task_created": result.task_created,
        "queue_position": position,
        "enqueued": bool(enqueued),
        "message": (
            f"诊断任务已提交，正在排队（前方还有 {position - 1} 个）"
            if position and position > 1
            else "诊断任务已提交，即将开始"
        )
        if result.task_created
        else "已有相同诊断在进行中，已复用",
    }
