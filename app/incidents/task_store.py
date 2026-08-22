"""diagnosis_tasks 的生命周期读写.

去重靠数据库的部分唯一索引而不是 SELECT-then-INSERT, 详见
``_create_or_get_task`` 里的说明。

``get_incident_group`` 放在这里而不是 ingest 侧: 调用方 (事件中心接口、deep 图的
correlation_context) 总是和 ``get_task`` 一起用, 都是"读事实"。
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.db.base import acquire
from app.db.base import json_dump, new_id
from app.incidents.models import DiagnosisMode, NormalizedAlert
from app.incidents.rows import record_to_dict, records_to_dicts

# 入队占位标记: claim_task_enqueue 写入它表示"某进程正在为该任务执行 XADD",
# 真实 Redis message id 回写后会覆盖它; 投递失败则释放回 NULL, 交给补偿扫描重投。
# Redis message id 形如 "1690000000000-0", 不会与该值混淆。
ENQUEUE_CLAIM_MARKER = "__claim__"


class TaskStoreMixin:
    """诊断任务的创建、状态流转、以及排队相关的读."""

    async def _create_or_get_task(
        self,
        conn: Any,
        *,
        incident_group_id: str,
        incident_id: str,
        query: str,
        alert: NormalizedAlert,
        diagnosis_mode: DiagnosisMode,
        priority: int,
    ) -> tuple[str, bool, bool]:
        # 去重幂等 (改造文档第 7 步): 不再 SELECT-then-INSERT (并发下两条相同告警可能
        # 同时查不到再各插一条). 改成依赖部分唯一索引 idx_diagnosis_task_dedup_active
        # (dedup_key WHERE status IN pending/running) 做 INSERT ON CONFLICT, 由数据库
        # 原子保证 "同一 dedup_key 活跃任务唯一". dedup_key 用 incident_group_id, 即
        # "一个告警组同时只有一个在跑的诊断", 与原行为一致但无竞态.
        task_id = new_id("task")
        dedup_key = incident_group_id
        payload = {
            "query": query,
            "alert_id": alert.id,
            "alertname": alert.alertname,
            "severity": alert.severity,
            "service": alert.service,
            "instance": alert.instance,
            "fingerprint": alert.fingerprint,
        }
        row = await conn.fetchrow(
            """
            INSERT INTO diagnosis_tasks (
                id, incident_group_id, incident_id, status, priority,
                diagnosis_mode, max_attempts, payload, dedup_key, last_seen_at
            )
            VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7::jsonb, $8, now())
            ON CONFLICT (dedup_key) WHERE status IN ('pending', 'running')
            DO UPDATE SET
                repeat_count = diagnosis_tasks.repeat_count + 1,
                last_seen_at = now(),
                updated_at = now()
            RETURNING
                id,
                (xmax = 0) AS inserted,
                status,
                queue_message_id
            """,
            task_id,
            incident_group_id,
            incident_id,
            priority,
            diagnosis_mode.value,
            settings.diagnosis_task_max_attempts,
            json_dump(payload),
            dedup_key,
        )
        # xmax = 0 表示这是真正的新插入; 否则是命中已有活跃任务被 DO UPDATE.
        returned_id = str(row["id"])
        task_created = bool(row["inserted"])
        # 复用命中已有任务时, 判断是否需要补投: 只有 "pending 且从未成功入队" 的
        # 复用任务才需要 (说明上次投递失败, 留下了幽灵任务); running 的复用归
        # 当前 Worker 管, 已带 queue_message_id 的 pending 复用说明消息还在路上。
        existing_message_id = str(row["queue_message_id"] or "")
        needs_enqueue = task_created or (
            str(row["status"] or "") == "pending" and not existing_message_id
        )
        return returned_id, task_created, needs_enqueue

    async def claim_task_enqueue(self, task_id: str) -> bool:
        """原子地认领一个任务的入队权, 防止并发投递产生重复消息.

        只有 status='pending' 且 queue_message_id 为空的任务能被认领;
        认领即写入占位标记 (见 ENQUEUE_CLAIM_MARKER), 竞争方 UPDATE 影响 0 行。
        """
        async with acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE diagnosis_tasks
                SET queue_message_id = $2, updated_at = now()
                WHERE id = $1
                  AND status = 'pending'
                  AND (queue_message_id IS NULL OR queue_message_id = '')
                RETURNING id
                """,
                task_id,
                ENQUEUE_CLAIM_MARKER,
            )
        return row is not None

    async def release_task_enqueue_claim(self, task_id: str) -> None:
        """投递失败后释放占位, 让任务回到 "可补投" 状态."""
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET queue_message_id = NULL, updated_at = now()
                WHERE id = $1 AND queue_message_id = $2
                """,
                task_id,
                ENQUEUE_CLAIM_MARKER,
            )

    async def list_unqueued_pending_tasks(
        self,
        older_than_sec: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """找出 "pending 但没有队列消息" 的任务 (幽灵任务), 供补偿重投.

        - queue_message_id 为空 或 停在占位标记: 从未成功入队, 或投递中途断掉;
        - updated_at 宽限期: 排除 "刚落库正在投递" 和 "刚被认领" 的任务,
          避免和提交方/重试路径竞态造成重复消息。
        """
        async with acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, incident_group_id, incident_id, status, priority,
                       diagnosis_mode, payload, attempts, max_attempts
                FROM diagnosis_tasks
                WHERE status = 'pending'
                  AND (
                      queue_message_id IS NULL
                      OR queue_message_id = ''
                      OR queue_message_id = $1
                  )
                  AND updated_at < now() - make_interval(secs => $2)
                ORDER BY created_at
                LIMIT $3
                """,
                ENQUEUE_CLAIM_MARKER,
                int(older_than_sec),
                max(1, int(limit)),
            )
        return records_to_dicts(rows)


    async def set_task_queue_message(self, task_id: str, message_id: str) -> None:
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET queue_message_id = $2, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                message_id,
            )

    async def mark_task_running(self, task_id: str) -> None:
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET status = 'running',
                    attempts = attempts + 1,
                    claimed_at = now(),
                    updated_at = now(),
                    error = ''
                WHERE id = $1
                """,
                task_id,
            )

    async def mark_task_succeeded(
        self,
        task_id: str,
        *,
        report: str = "",
        agent_run_id: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET status = 'succeeded',
                    finished_at = now(),
                    updated_at = now(),
                    payload = payload || $2::jsonb
                WHERE id = $1
                """,
                task_id,
                json_dump(
                    {
                        "report": report,
                        "agent_run_id": agent_run_id,
                        "evidence_ids": evidence_ids or [],
                    }
                ),
            )

    async def mark_task_failed(self, task_id: str, error: str) -> None:
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET status = 'failed',
                    error = $2,
                    finished_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                task_id,
                error[:4000],
            )

    async def mark_task_retry_pending(self, task_id: str, error: str) -> None:
        """Mark a failed attempt as retryable instead of final failed.

        为什么加在 repository:
        - diagnosis_tasks 的事实状态属于 Postgres, 不属于 Redis;
        - Worker 只决定"这次失败是否还能重试";
        - 具体怎么更新任务状态集中在 repository, 避免 SQL 散落在 Worker 里。

        预期效果:
        - 任务单次失败后回到 pending, 可以重新入队;
        - attempts 保留历史尝试次数, 便于到达 max_attempts 后进入 DLQ;
        - queue_message_id 清空: 旧消息即将被 ACK, 若后续重投失败,
          任务处于 "pending 且无消息" 状态, 会被补偿扫描重新投递。
        """
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET status = 'pending',
                    error = $2,
                    claimed_at = NULL,
                    queue_message_id = NULL,
                    updated_at = now()
                WHERE id = $1
                """,
                task_id,
                error[:4000],
            )


    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM diagnosis_tasks WHERE id = $1", task_id)
        return record_to_dict(row)

    async def get_incident_group(self, incident_group_id: str) -> dict[str, Any] | None:
        async with acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM incident_groups WHERE id = $1",
                incident_group_id,
            )
        return record_to_dict(row)


    async def queue_position(self, task_id: str) -> int | None:
        """估算某个 pending 任务的排队位置 (前面还有几个在排, 含自己 = 1-based)。

        口径: 按 created_at 先到先服务, 数所有 created_at <= 本任务且仍 pending 的任务数。
        running 的不计入 (已在被处理)。任务不存在 / 非 pending 返回 None。
        """
        async with acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS pos
                FROM diagnosis_tasks
                WHERE status = 'pending'
                  AND created_at <= (
                      SELECT created_at FROM diagnosis_tasks WHERE id = $1 AND status = 'pending'
                  )
                """,
                task_id,
            )
        if row is None or row["pos"] is None or int(row["pos"]) == 0:
            return None
        return int(row["pos"])

    async def list_recent_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        async with acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM diagnosis_tasks
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return records_to_dicts(rows)
