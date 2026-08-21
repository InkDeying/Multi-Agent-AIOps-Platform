"""删除一个终态任务, 并按引用关系级联清掉它独占的审计记录.

这段逻辑跨了四张不属于本模块的表 (tool_calls / agent_runs / approval_requests /
evidence), 所以单独成文件而不是混在任务状态流转里 —— 它是一次显式的跨域清理,
读的时候需要一眼看到边界在哪。

清理口径:
  - 排队中 / 进行中的任务不允许删 (抛 ValueError);
  - 该任务是所在 incident_group 的最后一个任务时, 删整个 group (连带孤儿 alerts);
  - 否则只删该任务自己的审计记录, 并在 incident 不再被任何任务引用时删 incident。
"""

from __future__ import annotations

from typing import Any

from app.db.base import acquire


class TaskPurgeMixin:
    """终态任务的删除与级联清理."""

    async def delete_task(self, task_id: str) -> dict[str, Any] | None:
        """Delete a terminal diagnosis task and its owned audit records.

        Evidence is scoped to an incident/group rather than directly to a task.
        When this is the last task for the group, deleting the group cascades the
        whole event. Otherwise, only an incident no longer referenced by another
        task is removed with its evidence.
        """
        async with acquire() as conn:
            async with conn.transaction():
                return await self._delete_task_with_conn(conn, task_id)

    async def delete_tasks(self, task_ids: list[str]) -> dict[str, Any]:
        """Delete several terminal tasks in one transaction and one API call."""
        unique_ids = sorted({task_id.strip() for task_id in task_ids if task_id.strip()})
        results: list[dict[str, Any]] = []
        skipped_active: list[str] = []
        not_found: list[str] = []

        async with acquire() as conn:
            async with conn.transaction():
                for task_id in unique_ids:
                    try:
                        result = await self._delete_task_with_conn(conn, task_id)
                    except ValueError:
                        skipped_active.append(task_id)
                        continue
                    if result is None:
                        not_found.append(task_id)
                    else:
                        results.append(result)

        return {
            "requested": len(unique_ids),
            "deleted": len(results),
            "skipped_active": skipped_active,
            "not_found": not_found,
            "items": results,
        }
    async def _delete_task_with_conn(
        self,
        conn: Any,
        task_id: str,
    ) -> dict[str, Any] | None:
        task = await conn.fetchrow(
            """
            SELECT id, status, incident_group_id, incident_id
            FROM diagnosis_tasks
            WHERE id = $1
            FOR UPDATE
            """,
            task_id,
        )
        if task is None:
            return None

        status = str(task["status"] or "")
        if status in {"pending", "running"}:
            raise ValueError("排队中或进行中的任务不能删除")

        group_id = str(task["incident_group_id"])
        incident_id = str(task["incident_id"])
        other_group_tasks = int(
            await conn.fetchval(
                """
                SELECT count(*)
                FROM diagnosis_tasks
                WHERE incident_group_id = $1 AND id <> $2
                """,
                group_id,
                task_id,
            )
            or 0
        )

        deleted_tool_calls = int(
            await conn.fetchval(
                "SELECT count(*) FROM tool_calls WHERE task_id = $1",
                task_id,
            )
            or 0
        )
        deleted_agent_runs = int(
            await conn.fetchval(
                "SELECT count(*) FROM agent_runs WHERE task_id = $1",
                task_id,
            )
            or 0
        )
        deleted_approvals = int(
            await conn.fetchval(
                "SELECT count(*) FROM approval_requests WHERE task_id = $1",
                task_id,
            )
            or 0
        )

        if other_group_tasks == 0:
            alert_ids = await conn.fetch(
                """
                SELECT alert_id
                FROM incident_group_alerts
                WHERE incident_group_id = $1
                """,
                group_id,
            )
            deleted_evidence = int(
                await conn.fetchval(
                    "SELECT count(*) FROM evidence WHERE incident_group_id = $1",
                    group_id,
                )
                or 0
            )
            await conn.execute(
                "DELETE FROM approval_requests WHERE incident_group_id = $1",
                group_id,
            )
            await conn.execute("DELETE FROM incident_groups WHERE id = $1", group_id)
            orphan_alert_ids = [str(row["alert_id"]) for row in alert_ids]
            if orphan_alert_ids:
                await conn.execute(
                    """
                    DELETE FROM alerts a
                    WHERE a.id = ANY($1::text[])
                      AND NOT EXISTS (
                          SELECT 1
                          FROM incident_group_alerts iga
                          WHERE iga.alert_id = a.id
                      )
                    """,
                    orphan_alert_ids,
                )
            return {
                "task_id": task_id,
                "incident_group_id": group_id,
                "group_deleted": True,
                "deleted_evidence": deleted_evidence,
                "deleted_agent_runs": deleted_agent_runs,
                "deleted_tool_calls": deleted_tool_calls,
                "deleted_approvals": deleted_approvals,
            }

        await conn.execute("DELETE FROM approval_requests WHERE task_id = $1", task_id)
        await conn.execute("DELETE FROM tool_calls WHERE task_id = $1", task_id)
        await conn.execute("DELETE FROM agent_runs WHERE task_id = $1", task_id)
        await conn.execute("DELETE FROM diagnosis_tasks WHERE id = $1", task_id)

        other_incident_tasks = int(
            await conn.fetchval(
                "SELECT count(*) FROM diagnosis_tasks WHERE incident_id = $1",
                incident_id,
            )
            or 0
        )
        deleted_evidence = 0
        if other_incident_tasks == 0:
            deleted_evidence = int(
                await conn.fetchval(
                    "SELECT count(*) FROM evidence WHERE incident_id = $1",
                    incident_id,
                )
                or 0
            )
            await conn.execute("DELETE FROM evidence WHERE incident_id = $1", incident_id)
            await conn.execute("DELETE FROM incidents WHERE id = $1", incident_id)

        return {
            "task_id": task_id,
            "incident_group_id": group_id,
            "group_deleted": False,
            "deleted_evidence": deleted_evidence,
            "deleted_agent_runs": deleted_agent_runs,
            "deleted_tool_calls": deleted_tool_calls,
            "deleted_approvals": deleted_approvals,
        }
