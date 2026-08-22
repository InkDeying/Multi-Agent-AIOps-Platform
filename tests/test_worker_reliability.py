"""批次三可靠性修复的契约测试: 工具调用超时与 worker 轮询隔离。"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.harness.runtime.tool_runner import _invoke_tool


class _FakeAsyncTool:
    """带 ainvoke 的假工具, 用于验证超时包裹。"""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def ainvoke(self, args):  # noqa: ANN001
        await asyncio.sleep(self._delay)
        return "ok"


class _FakeSyncTool:
    def invoke(self, args):  # noqa: ANN001
        return "sync-ok"


class InvokeToolTimeoutTests(unittest.TestCase):
    def test_async_tool_times_out(self) -> None:
        settings = mock.MagicMock()
        settings.mcp_tool_timeout_sec = 0.05
        # _invoke_tool 内部延迟 import app.config.settings, patch 全局单例即可生效
        with mock.patch("app.config.settings", settings):
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(_invoke_tool(_FakeAsyncTool(delay=1.0), {}))

    def test_fast_async_tool_passes(self) -> None:
        settings = mock.MagicMock()
        settings.mcp_tool_timeout_sec = 5
        with mock.patch("app.config.settings", settings):
            result = asyncio.run(_invoke_tool(_FakeAsyncTool(delay=0), {}))
        self.assertEqual(result, "ok")

    def test_sync_tool_unaffected(self) -> None:
        settings = mock.MagicMock()
        settings.mcp_tool_timeout_sec = 0.05
        with mock.patch("app.config.settings", settings):
            result = asyncio.run(_invoke_tool(_FakeSyncTool(), {}))
        self.assertEqual(result, "sync-ok")


class WorkerPollIsolationTests(unittest.TestCase):
    def test_poll_once_propagates_so_start_loop_catches(self) -> None:
        """_poll_once 的异常向上传播 (由 start 的 try 捕获, 不再杀死进程)。"""
        from app.diagnosis_worker import DiagnosisWorker

        worker = DiagnosisWorker(consumer_name="test")
        # 直接让轮询内部某个 await 抛错 (选 claim 阶段, 它没有内部自吞)
        with mock.patch.object(
            worker,
            "_claim_stale_tasks_once",
            new=mock.AsyncMock(side_effect=RuntimeError("pg down")),
        ), mock.patch.object(worker, "_requeue_unqueued_once", new=mock.AsyncMock()):
            with self.assertRaises(RuntimeError):
                asyncio.run(worker._poll_once())

    def test_requeue_guard_keeps_poll_alive(self) -> None:
        """补偿扫描内部自吞异常: _poll_once 不应因此中断。"""
        import app.diagnosis_worker as worker_module
        from app.diagnosis_worker import DiagnosisWorker

        worker = DiagnosisWorker(consumer_name="test")
        scan = mock.AsyncMock(side_effect=RuntimeError("pg down"))
        read = mock.AsyncMock(return_value=[])
        with mock.patch(worker_module.__name__ + ".requeue_unqueued_pending_tasks", scan), \
                mock.patch.object(worker, "_claim_stale_tasks_once", new=mock.AsyncMock(return_value=[])), \
                mock.patch("app.diagnosis_worker.incident_queue.read_tasks", read):
            asyncio.run(worker._poll_once())  # 不抛即通过


if __name__ == "__main__":
    unittest.main()
