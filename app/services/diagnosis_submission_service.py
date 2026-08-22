"""手动诊断排队用例服务."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.config import settings
from app.incidents.dispatch import dispatch_diagnosis_task
from app.incidents.models import DiagnosisMode
from app.incidents.repository import incident_repository
from app.queue.redis_streams import level_for_severity


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

    # needs_enqueue 而不是 task_created: 复用 "pending 且从未入队" 的任务时
    # 也要补投, 否则上次入队失败留下的任务会永远 pending (幽灵任务)。
    message_id: str | None = None
    if settings.incident_pipeline_enabled and result.needs_enqueue:
        message_id = await dispatch_diagnosis_task(
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
        if message_id is None:
            logger.warning(
                f"[submit] task={result.task_id} not enqueued now; "
                "worker reconciler will retry"
            )

    position = await incident_repository.queue_position(result.task_id)
    if not result.task_created:
        status = "running"
        message = "已有相同诊断在进行中，已复用"
    elif message_id:
        status = "queued"
        message = (
            f"诊断任务已提交，正在排队（前方还有 {position - 1} 个）"
            if position and position > 1
            else "诊断任务已提交，即将开始"
        )
    else:
        status = "pending"
        message = "诊断任务已保存，暂时未能入队；Worker 会自动补偿投递，无需重复提交"
    return {
        "task_id": result.task_id,
        "incident_group_id": result.incident_group_id,
        "status": status,
        "task_created": result.task_created,
        "queue_position": position,
        "enqueued": bool(message_id),
        "message": message,
    }
