"""Load and normalize the incident facts needed by the deep graph."""

from typing import Any

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_INCIDENT_LOADED, make_transition


async def incident_manager_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """载入诊断对象并补齐图状态。

    Worker 路径通常带有 ``task_id``，此时回查 Postgres 任务事实；手动 SSE
    没有持久化任务时直接放行。数据库异常只写 transition，不阻断后续取证。
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
    try:
        # 延迟导入让 deep graph 在没有数据库依赖时仍可构建。
        from app.incidents.repository import incident_repository

        task = await incident_repository.get_task(task_id)
        if task is None:
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
        # Worker 通常已经填过 group/incident；这里保留双保险补齐逻辑。
        if not incident_group_id and task.get("incident_group_id"):
            patch["incident_group_id"] = str(task["incident_group_id"])
        if not state.get("incident_id") and task.get("incident_id"):
            patch["incident_id"] = str(task["incident_id"])
        return patch
    except Exception as exc:
        # DB 故障降级：deep 图仍应产出后续证据和报告。
        logger.exception(f"[deep] IncidentManager DB 查询失败: {exc}")
        return {
            "transition_history": [
                make_transition(
                    "incident_manager",
                    DEEP_INCIDENT_LOADED,
                    f"{detail} db_error: {type(exc).__name__}",
                )
            ],
        }
