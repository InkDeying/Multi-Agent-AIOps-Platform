"""读取编排层注入的任务事实并补齐 Deep 图状态。"""

from typing import Any

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_INCIDENT_LOADED, make_transition


async def incident_manager_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """载入诊断对象并补齐图状态。

    Worker 路径通常带有 ``task_id``，任务事实已由 orchestration 预加载；手动 SSE
    没有持久化任务时直接放行。读取异常只写 transition，不阻断后续取证。
    """
    task_id = state.get("task_id") or ""
    incident_group_id = state.get("incident_group_id") or ""

    if not task_id:
        # 手动诊断路径没有 task 事实，后续节点直接使用 state.input。
        logger.info("[deep] IncidentManager: no task_id (manual SSE path)")
        return {
            "transition_history": [
                make_transition(
                    "incident_manager",
                    DEEP_INCIDENT_LOADED,
                    "no task_id (manual path)",
                )
            ],
        }

    detail = f"task={task_id}"
    status = state.get("task_context_status") or "not_found"
    if status == "error":
        error_type = state.get("task_context_error_type") or "UnknownError"
        logger.warning(f"[deep] IncidentManager: task context error={error_type}")
        return {
            "transition_history": [
                make_transition(
                    "incident_manager",
                    DEEP_INCIDENT_LOADED,
                    f"{detail} db_error: {error_type}",
                )
            ],
        }

    task = state.get("task_context") or {}
    if status != "loaded" or not task:
        logger.warning(f"[deep] IncidentManager: task {task_id} not found in DB")
        return {
            "transition_history": [
                make_transition(
                    "incident_manager",
                    DEEP_INCIDENT_LOADED,
                    f"task {task_id} not found",
                )
            ],
        }

    # alert_signature 已由 runner 计算，这里只透传任务中的上下文。
    payload = task.get("payload") or {}
    patch: dict[str, Any] = {
        "transition_history": [
            make_transition(
                "incident_manager",
                DEEP_INCIDENT_LOADED,
                f"{detail} alertname={payload.get('alertname', '-')} "
                f"severity={payload.get('severity', '-')}",
            )
        ],
    }
    # 保留直接调用节点时的双保险补齐逻辑；常规运行中编排层已经完成补齐。
    if not incident_group_id and task.get("incident_group_id"):
        patch["incident_group_id"] = str(task["incident_group_id"])
    if not state.get("incident_id") and task.get("incident_id"):
        patch["incident_id"] = str(task["incident_id"])
    return patch
