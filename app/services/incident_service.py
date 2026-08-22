"""Incident 与诊断任务用例服务.

API 层只负责请求模型和 HTTP 异常映射；事实仓储与队列编排集中在这里。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.config import settings
from app.evidence.repository import evidence_repository
from app.incidents.dispatch import dispatch_diagnosis_task
from app.incidents.models import DiagnosisMode
from app.incidents.repository import incident_repository
from app.db.agent_runs import agent_run_repository
from app.queue.redis_streams import level_for_severity


async def list_tasks(limit: int = 20) -> dict[str, Any]:
    items = await incident_repository.list_recent_tasks(limit=limit)
    return {"count": len(items), "items": items}


async def bulk_delete_tasks(task_ids: list[str]) -> dict[str, Any]:
    return await incident_repository.delete_tasks(task_ids)


async def get_task(task_id: str) -> dict[str, Any] | None:
    task = await incident_repository.get_task(task_id)
    if task is not None and task.get("status") == "pending":
        task["queue_position"] = await incident_repository.queue_position(task_id)
    return task


async def delete_task(task_id: str) -> dict[str, Any] | None:
    return await incident_repository.delete_task(task_id)


async def list_task_agent_runs(task_id: str) -> dict[str, Any]:
    items = await _require_task_and_get(
        task_id,
        agent_run_repository.list_runs_for_task,
    )
    return {"count": len(items), "items": items}


async def list_task_tool_calls(task_id: str) -> dict[str, Any]:
    items = await _require_task_and_get(
        task_id,
        agent_run_repository.list_tool_calls_for_task,
    )
    return {"count": len(items), "items": items}


async def list_task_evidence(task_id: str, limit: int = 100) -> dict[str, Any]:
    await _require_task(task_id)
    items = await evidence_repository.list_for_task(task_id, limit=limit)
    return {"count": len(items), "items": items}


async def create_incident_from_chat(
    *,
    session_id: str,
    query: str,
    title: str,
    severity: str,
    service: str,
    diagnosis_mode: str,
    chat_excerpt: str = "",
    rag_hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        mode = DiagnosisMode(diagnosis_mode.lower().strip())
    except ValueError:  # 未知模式降级 FAST; 不裸捕获, 避免掩盖其他 bug
        mode = DiagnosisMode.FAST

    context: dict[str, Any] = {"session_id": session_id}
    if chat_excerpt:
        context["chat_excerpt"] = chat_excerpt[:4000]
    if rag_hits:
        context["rag_hits"] = rag_hits[:10]

    result = await incident_repository.create_manual_task(
        source=f"chat:{session_id}",
        title=title or query[:80],
        query=query,
        severity=severity or "warning",
        service=service or "",
        diagnosis_mode=mode,
        context=context,
    )

    # needs_enqueue 而不是 task_created: 复用 "pending 且从未入队" 的任务时
    # 也要补投, 避免上次入队失败的任务永远 pending (幽灵任务)。
    message_id: str | None = None
    if settings.incident_pipeline_enabled and result.needs_enqueue:
        message_id = await dispatch_diagnosis_task(
            task_id=result.task_id,
            incident_group_id=result.incident_group_id,
            incident_id=result.incident_id,
            diagnosis_mode=mode.value,
            priority=100,
            level=level_for_severity(severity),
            payload={
                "query": query,
                "alertname": title or query[:80],
                "severity": severity,
                "service": service,
                "source": f"chat:{session_id}",
            },
        )
        if message_id is None:
            logger.warning(
                f"[from_chat] task={result.task_id} not enqueued now; "
                "worker reconciler will retry"
            )

    return {
        "task_id": result.task_id,
        "incident_group_id": result.incident_group_id,
        "incident_id": result.incident_id,
        "task_created": result.task_created,
        "queue_message_id": message_id or "",
        "enqueued": bool(message_id),
    }


async def get_incident_group(incident_group_id: str) -> dict[str, Any] | None:
    return await incident_repository.get_incident_group(incident_group_id)


async def list_incident_group_evidence(
    incident_group_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    items = await evidence_repository.list_for_incident_group(
        incident_group_id,
        limit=limit,
    )
    return {"count": len(items), "items": items}


async def _require_task(task_id: str) -> dict[str, Any]:
    task = await incident_repository.get_task(task_id)
    if task is None:
        raise LookupError("task not found")
    return task


async def _require_task_and_get(task_id: str, getter: Any) -> list[dict[str, Any]]:
    await _require_task(task_id)
    return await getter(task_id)
