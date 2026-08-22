"""diagnosis_task 的入队派发: Postgres 占位 → Redis XADD → 回写消息 id.

解决的问题 ("幽灵任务"): 任务先落 Postgres、再 XADD Redis, 中间失败会留下
"pending 但队列里没有消息" 的任务; 去重索引让后续相同请求只复用不补投,
任务就永久 pending。本模块把投递变成一个可补偿的三步协议:

  1. ``claim_task_enqueue`` 原子占位 (UPDATE ... WHERE queue_message_id 为空),
     并发提交/补偿扫描竞争时只有一个赢家, 避免重复消息 → 重复执行;
  2. XADD 成功后回写真实 message id, 占位被覆盖;
  3. XADD 失败则释放占位回到 NULL, 由 ``requeue_unqueued_pending_tasks``
     (Worker 补偿扫描) 在宽限期后重投。

语义与队列其余恢复机制的关系:
- XAUTOCLAIM 只能回收 "已投递未 ACK" 的消息, 覆盖不到从未入队的任务;
- 本模块的补偿扫描以 Postgres 事实为准, 是它的补集。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.incidents.repository import incident_repository
from app.queue.redis_streams import incident_queue


async def dispatch_diagnosis_task(
    *,
    task_id: str,
    incident_group_id: str,
    incident_id: str,
    diagnosis_mode: str,
    priority: int,
    payload: dict[str, Any],
    level: str | None = None,
) -> str | None:
    """把一个 pending 任务投递进 Redis Stream, 返回 message id.

    返回 None 表示没有投递 (claim 被竞争方持有, 或 XADD 失败已释放占位);
    调用方应如实上报 enqueued=False, 任务留给补偿扫描兜底, 不会丢失。
    """
    claimed = await incident_repository.claim_task_enqueue(task_id)
    if not claimed:
        logger.info(
            f"[dispatch] task={task_id} skip enqueue: "
            "already queued or another dispatcher holds the claim"
        )
        return None

    try:
        message_id = await incident_queue.enqueue_task(
            task_id=task_id,
            incident_group_id=incident_group_id,
            incident_id=incident_id,
            diagnosis_mode=diagnosis_mode,
            priority=priority,
            payload=payload,
            level=level,
        )
    except Exception as exc:
        logger.exception(
            f"[dispatch] task={task_id} enqueue failed "
            f"({type(exc).__name__}: {exc}); claim released, reconciler will retry"
        )
        try:
            await incident_repository.release_task_enqueue_claim(task_id)
        except Exception as release_exc:
            # 释放失败时占位标记会留在行上, 补偿扫描同样会把占位行视为可重投。
            logger.warning(
                f"[dispatch] task={task_id} release claim failed: {release_exc}"
            )
        return None

    await incident_repository.set_task_queue_message(task_id, message_id)
    return message_id


async def requeue_unqueued_pending_tasks(
    *,
    grace_sec: int,
    batch_size: int = 50,
) -> int:
    """补偿扫描: 重投 "pending 且从未成功入队" 的任务, 返回重投成功数。

    由 Worker 主循环周期调用。宽限期内 (默认 60s) 的行不会被扫到,
    以避开 "刚落库正在投递 / 刚被认领" 的正常窗口。
    """
    rows = await incident_repository.list_unqueued_pending_tasks(
        grace_sec, batch_size
    )
    if not rows:
        return 0

    requeued = 0
    for row in rows:
        task_id = str(row.get("id") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        message_id = await dispatch_diagnosis_task(
            task_id=task_id,
            incident_group_id=str(row.get("incident_group_id") or ""),
            incident_id=str(row.get("incident_id") or ""),
            diagnosis_mode=str(row.get("diagnosis_mode") or "fast"),
            priority=int(row.get("priority") or 100),
            payload=payload,
        )
        if message_id:
            requeued += 1
            logger.warning(
                f"[dispatch] requeued unqueued pending task={task_id} "
                f"message={message_id} attempts={row.get('attempts')}"
            )

    if requeued:
        logger.warning(
            f"[dispatch] reconciled {requeued}/{len(rows)} pending task(s) "
            "without queue message"
        )
    return requeued
