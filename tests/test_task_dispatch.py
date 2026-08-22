"""入队派发与幽灵任务补偿的行为契约测试.

覆盖:
- dispatch 协议三步: 原子占位 → XADD → 回写; 占位失败/投递失败的行为;
- 补偿扫描: 只重投扫描到的行, 汇总成功数;
- 三条提交路径 (submit / from_chat / webhook) 的诚实上报与复用补投;
- Worker 补偿扫描异常不打断主循环、遵守间隔控制。
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app.incidents import dispatch as dispatch_module
from app.incidents.dispatch import (
    dispatch_diagnosis_task,
    requeue_unqueued_pending_tasks,
)


def _ingest_result(**overrides) -> SimpleNamespace:
    base = dict(
        alert_id="al-1",
        incident_group_id="ig-1",
        incident_id="inc-1",
        correlation_key="ck-1",
        task_id="task-1",
        task_created=True,
        needs_enqueue=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class DispatchProtocolTests(unittest.TestCase):
    def test_skips_enqueue_when_claim_lost(self) -> None:
        repo, queue = mock.AsyncMock(), mock.AsyncMock()
        repo.claim_task_enqueue.return_value = False
        with mock.patch.object(dispatch_module, "incident_repository", repo), \
                mock.patch.object(dispatch_module, "incident_queue", queue):
            result = asyncio.run(
                dispatch_diagnosis_task(
                    task_id="task-1",
                    incident_group_id="ig-1",
                    incident_id="inc-1",
                    diagnosis_mode="fast",
                    priority=100,
                    payload={},
                )
            )
        self.assertIsNone(result)
        queue.enqueue_task.assert_not_awaited()

    def test_claims_enqueues_then_records_message_id(self) -> None:
        repo, queue = mock.AsyncMock(), mock.AsyncMock()
        repo.claim_task_enqueue.return_value = True
        queue.enqueue_task.return_value = "1700000000000-0"
        with mock.patch.object(dispatch_module, "incident_repository", repo), \
                mock.patch.object(dispatch_module, "incident_queue", queue):
            result = asyncio.run(
                dispatch_diagnosis_task(
                    task_id="task-1",
                    incident_group_id="ig-1",
                    incident_id="inc-1",
                    diagnosis_mode="fast",
                    priority=100,
                    payload={"severity": "critical"},
                )
            )
        self.assertEqual(result, "1700000000000-0")
        repo.set_task_queue_message.assert_awaited_once_with(
            "task-1", "1700000000000-0"
        )
        repo.release_task_enqueue_claim.assert_not_awaited()

    def test_releases_claim_when_xadd_fails(self) -> None:
        repo, queue = mock.AsyncMock(), mock.AsyncMock()
        repo.claim_task_enqueue.return_value = True
        queue.enqueue_task.side_effect = RuntimeError("redis down")
        with mock.patch.object(dispatch_module, "incident_repository", repo), \
                mock.patch.object(dispatch_module, "incident_queue", queue):
            result = asyncio.run(
                dispatch_diagnosis_task(
                    task_id="task-1",
                    incident_group_id="ig-1",
                    incident_id="inc-1",
                    diagnosis_mode="fast",
                    priority=100,
                    payload={},
                )
            )
        self.assertIsNone(result)
        repo.release_task_enqueue_claim.assert_awaited_once_with("task-1")
        repo.set_task_queue_message.assert_not_awaited()


class RequeueScanTests(unittest.TestCase):
    def _row(self, task_id: str) -> dict:
        return {
            "id": task_id,
            "incident_group_id": "ig-1",
            "incident_id": "inc-1",
            "diagnosis_mode": "fast",
            "priority": 100,
            "payload": {"query": "q", "severity": "warning"},
            "attempts": 0,
        }

    def test_requeues_each_stale_row_and_counts_success(self) -> None:
        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.list_unqueued_pending_tasks.return_value = [
            self._row("task-1"),
            self._row("task-2"),
        ]
        dsp.side_effect = ["m-1", None]
        with mock.patch.object(dispatch_module, "incident_repository", repo), \
                mock.patch.object(dispatch_module, "dispatch_diagnosis_task", dsp):
            count = asyncio.run(
                requeue_unqueued_pending_tasks(grace_sec=60, batch_size=50)
            )
        self.assertEqual(count, 1)
        self.assertEqual(dsp.await_count, 2)

    def test_no_rows_means_no_dispatch(self) -> None:
        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.list_unqueued_pending_tasks.return_value = []
        with mock.patch.object(dispatch_module, "incident_repository", repo), \
                mock.patch.object(dispatch_module, "dispatch_diagnosis_task", dsp):
            count = asyncio.run(
                requeue_unqueued_pending_tasks(grace_sec=60, batch_size=50)
            )
        self.assertEqual(count, 0)
        dsp.assert_not_awaited()


class SubmitServiceHonestyTests(unittest.TestCase):
    def _run(self, dispatch_return, needs_enqueue=True, task_created=True):
        from app.services import diagnosis_submission_service as svc

        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.create_manual_task.return_value = _ingest_result(
            task_created=task_created, needs_enqueue=needs_enqueue
        )
        repo.queue_position.return_value = 1
        dsp.return_value = dispatch_return
        settings = SimpleNamespace(incident_pipeline_enabled=True)
        with mock.patch.object(svc, "incident_repository", repo), \
                mock.patch.object(svc, "dispatch_diagnosis_task", dsp), \
                mock.patch.object(svc, "settings", settings):
            data = asyncio.run(
                svc.submit(
                    query="q",
                    mode="fast",
                    session_id="s",
                    severity="warning",
                    service="",
                )
            )
        return data, dsp

    def test_new_task_reports_queued_when_enqueued(self) -> None:
        data, dsp = self._run("m-1")
        self.assertEqual(data["status"], "queued")
        self.assertTrue(data["enqueued"])
        dsp.assert_awaited_once()

    def test_enqueue_failure_reports_pending_not_queued(self) -> None:
        data, dsp = self._run(None)
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["enqueued"])
        self.assertIn("补偿", data["message"])
        dsp.assert_awaited_once()

    def test_reuse_of_never_enqueued_task_still_dispatches(self) -> None:
        data, dsp = self._run("m-2", needs_enqueue=True, task_created=False)
        self.assertFalse(data["task_created"])
        self.assertTrue(data["enqueued"])
        dsp.assert_awaited_once()

    def test_reuse_of_running_task_does_not_dispatch(self) -> None:
        data, dsp = self._run(None, needs_enqueue=False, task_created=False)
        self.assertEqual(data["status"], "running")
        self.assertFalse(data["enqueued"])
        dsp.assert_not_awaited()


class FromChatServiceTests(unittest.TestCase):
    def test_from_chat_requeues_never_enqueued_reuse(self) -> None:
        from app.services import incident_service as svc

        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.create_manual_task.return_value = _ingest_result(
            task_created=False, needs_enqueue=True
        )
        dsp.return_value = "m-9"
        settings = SimpleNamespace(incident_pipeline_enabled=True)
        with mock.patch.object(svc, "incident_repository", repo), \
                mock.patch.object(svc, "dispatch_diagnosis_task", dsp), \
                mock.patch.object(svc, "settings", settings):
            data = asyncio.run(
                svc.create_incident_from_chat(
                    session_id="s",
                    query="q",
                    title="",
                    severity="warning",
                    service="",
                    diagnosis_mode="fast",
                )
            )
        self.assertTrue(data["enqueued"])
        self.assertEqual(data["queue_message_id"], "m-9")
        dsp.assert_awaited_once()


class WebhookServiceTests(unittest.TestCase):
    def _payload(self):
        return SimpleNamespace(
            receiver="default",
            alerts=[
                SimpleNamespace(
                    status="firing",
                    labels={"alertname": "HighCPU", "severity": "warning", "instance": "i-1"},
                    annotations={},
                    startsAt="",
                    endsAt="",
                    generatorURL="",
                    fingerprint="fp-1",
                )
            ],
        )

    def test_ingestion_failure_goes_to_failed_list(self) -> None:
        from app.services import webhook_service as svc

        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.ingest_alertmanager_alert.side_effect = RuntimeError("pg down")
        with mock.patch.object(svc, "incident_repository", repo), \
                mock.patch.object(svc, "dispatch_diagnosis_task", dsp):
            data = asyncio.run(svc.process_alertmanager_payload(self._payload()))
        self.assertEqual(len(data["failed"]), 1)
        self.assertEqual(data["accepted"], [])
        dsp.assert_not_awaited()

    def test_enqueue_deferred_still_reports_accepted_not_failed(self) -> None:
        from app.services import webhook_service as svc

        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.ingest_alertmanager_alert.return_value = _ingest_result()
        dsp.return_value = None
        with mock.patch.object(svc, "incident_repository", repo), \
                mock.patch.object(svc, "dispatch_diagnosis_task", dsp):
            data = asyncio.run(svc.process_alertmanager_payload(self._payload()))
        self.assertEqual(data["failed"], [])
        self.assertEqual(len(data["accepted"]), 1)
        self.assertFalse(data["accepted"][0]["enqueued"])
        dsp.assert_awaited_once()

    def test_reuse_without_enqueue_need_skips_dispatch(self) -> None:
        from app.services import webhook_service as svc

        repo, dsp = mock.AsyncMock(), mock.AsyncMock()
        repo.ingest_alertmanager_alert.return_value = _ingest_result(
            task_created=False, needs_enqueue=False
        )
        with mock.patch.object(svc, "incident_repository", repo), \
                mock.patch.object(svc, "dispatch_diagnosis_task", dsp):
            data = asyncio.run(svc.process_alertmanager_payload(self._payload()))
        self.assertEqual(len(data["accepted"]), 1)
        self.assertFalse(data["accepted"][0]["enqueued"])
        dsp.assert_not_awaited()


class WorkerRequeueGuardTests(unittest.TestCase):
    def test_requeue_scan_failure_does_not_raise(self) -> None:
        import app.diagnosis_worker as worker_module
        from app.diagnosis_worker import DiagnosisWorker

        worker = DiagnosisWorker(consumer_name="test")
        worker._last_requeue_monotonic = time.monotonic() - 3600
        scan = mock.AsyncMock(side_effect=RuntimeError("pg down"))
        with mock.patch.object(worker_module, "requeue_unqueued_pending_tasks", scan):
            asyncio.run(worker._requeue_unqueued_once())
        scan.assert_awaited_once()

    def test_requeue_respects_interval_gate(self) -> None:
        import app.diagnosis_worker as worker_module
        from app.diagnosis_worker import DiagnosisWorker

        worker = DiagnosisWorker(consumer_name="test")
        worker._last_requeue_monotonic = time.monotonic()
        scan = mock.AsyncMock()
        with mock.patch.object(worker_module, "requeue_unqueued_pending_tasks", scan):
            asyncio.run(worker._requeue_unqueued_once())
        scan.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
