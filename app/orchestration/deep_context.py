"""为 Deep Diagnosis 预加载任务、事件组和 Wiki 上下文。

Deep 图节点只负责基于已注入的状态做确定性转换，不直接访问 Postgres 或文件系统。
本模块是编排层的数据装配点：各数据源均按 best-effort 读取，并把结果状态显式写入
graph state，以保留手动诊断、事实缺失和基础设施异常时的原有降级语义。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.harness.wiki.store import recall_block
from app.incidents.repository import incident_repository


async def build_deep_context(
    *,
    query: str,
    task_id: str = "",
    incident_group_id: str = "",
    incident_id: str = "",
    alert_signature: str = "",
) -> dict[str, Any]:
    """构建 Deep 图输入上下文，任何单一数据源失败都不阻断诊断。"""
    patch: dict[str, Any] = {
        "task_id": task_id,
        "incident_group_id": incident_group_id,
        "incident_id": incident_id,
        "task_context": {},
        "task_context_status": "manual" if not task_id else "not_found",
        "task_context_error_type": "",
        "incident_group_context": {},
        "incident_group_context_status": "manual",
        "incident_group_context_error_type": "",
        "wiki_context": "",
    }

    if task_id:
        try:
            task = await incident_repository.get_task(task_id)
            if task is None:
                logger.warning(f"[deep-context] task {task_id} not found")
            else:
                patch["task_context"] = task
                patch["task_context_status"] = "loaded"
                if not incident_group_id and task.get("incident_group_id"):
                    patch["incident_group_id"] = str(task["incident_group_id"])
                if not incident_id and task.get("incident_id"):
                    patch["incident_id"] = str(task["incident_id"])
        except Exception as exc:
            patch["task_context_status"] = "error"
            patch["task_context_error_type"] = type(exc).__name__
            logger.exception(f"[deep-context] task 查询失败: {exc}")

    try:
        patch["wiki_context"] = await recall_block(
            query=query,
            signature=alert_signature,
        )
    except Exception as exc:
        logger.warning(
            f"[deep-context] wiki recall failed (ignored): "
            f"{type(exc).__name__}: {exc}"
        )

    resolved_group_id = str(patch.get("incident_group_id") or "")
    if not resolved_group_id:
        return patch

    patch["incident_group_context_status"] = "not_found"
    try:
        group = await incident_repository.get_incident_group(resolved_group_id)
        if group is None:
            logger.warning(f"[deep-context] group {resolved_group_id} not found")
        else:
            patch["incident_group_context"] = group
            patch["incident_group_context_status"] = "loaded"
    except Exception as exc:
        patch["incident_group_context_status"] = "error"
        patch["incident_group_context_error_type"] = type(exc).__name__
        logger.exception(f"[deep-context] incident group 查询失败: {exc}")

    return patch
