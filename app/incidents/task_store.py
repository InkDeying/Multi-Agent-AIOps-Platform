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
    ) -> tuple[str, bool]:
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
            RETURNING id, (xmax = 0) AS inserted
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
        return returned_id, task_created


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
        - attempts 保留历史尝试次数, 便于到达 max_attempts 后进入 DLQ。
        """
        async with acquire() as conn:
            await conn.execute(
                """
                UPDATE diagnosis_tasks
                SET status = 'pending',
                    error = $2,
                    claimed_at = NULL,
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
