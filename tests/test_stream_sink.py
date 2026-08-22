"""stream_sink 事件旁路与结束哨兵投递的行为契约测试.

回归背景 (幽灵挂起缺陷):
- 旧实现哨兵用 put_nowait 且吞掉 QueueFull —— 队列满时哨兵丢失, 消费者
  排空后永久阻塞在 get(), SSE 永不结束;
- 旧实现 emit 满队列静默丢 token —— 答案被静默截断, 且只在"一个 token
  都没收到"时才有兜底。
"""

from __future__ import annotations

import asyncio
import unittest

from app.harness.runtime.stream_sink import emit, put_bounded, set_sink


class EmitModeTests(unittest.TestCase):
    def test_default_mode_drops_when_full(self) -> None:
        async def scenario() -> int:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            set_sink(q)  # 默认: 丢弃模式 (诊断流行为)
            await emit({"type": "step_token", "content": "a"})
            await emit({"type": "step_token", "content": "b"})  # 满即丢, 不阻塞
            return q.qsize()

        self.assertEqual(asyncio.run(scenario()), 1)

    def test_block_mode_is_lossless_under_backpressure(self) -> None:
        """无损模式: 事件数远大于 maxsize 时, 生产者等待消费者, 一个不丢."""

        async def scenario() -> list[str]:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
            set_sink(q, block_on_full=True)

            async def produce() -> None:
                for i in range(10):
                    await emit({"type": "step_token", "content": str(i)})

            async def consume() -> list[str]:
                return [
                    (await q.get())["content"] for _ in range(10)
                ]

            producer = asyncio.create_task(produce())
            received = await consume()
            await producer
            return received

        self.assertEqual(
            asyncio.run(scenario()),
            [str(i) for i in range(10)],
        )


class PutBoundedTests(unittest.TestCase):
    def test_delivers_immediately_when_space_available(self) -> None:
        async def scenario() -> tuple[bool, dict]:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            ok = await put_bounded(q, {"__sentinel__": 1}, timeout_sec=1)
            return ok, await q.get()

        ok, event = asyncio.run(scenario())
        self.assertTrue(ok)
        self.assertEqual(event, {"__sentinel__": 1})

    def test_gives_up_after_timeout_when_queue_stays_full(self) -> None:
        """消费者已退出 (无人排空) 时必须放弃, 不能永久阻塞 —— 死锁修复的核心."""

        async def scenario() -> bool:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            q.put_nowait({"filler": True})
            return await put_bounded(q, {"__sentinel__": 1}, timeout_sec=0.05)

        self.assertFalse(asyncio.run(scenario()))

    def test_delivers_once_consumer_drains_full_queue(self) -> None:
        """满队列 + 存活消费者: 排空腾出空位后哨兵必然送达."""

        async def scenario() -> bool:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            q.put_nowait({"filler": True})

            async def drain_soon() -> None:
                await asyncio.sleep(0.01)
                await q.get()

            drainer = asyncio.create_task(drain_soon())
            ok = await put_bounded(q, {"__sentinel__": 1}, timeout_sec=2)
            await drainer
            return ok

        self.assertTrue(asyncio.run(scenario()))

    def test_cancellation_does_not_hang(self) -> None:
        """取消已阻塞的 put_bounded 任务应立即结束, 不产生悬挂协程."""

        async def scenario() -> str:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            q.put_nowait({"filler": True})
            task = asyncio.create_task(
                put_bounded(q, {"__sentinel__": 1}, timeout_sec=5)
            )
            await asyncio.sleep(0.01)  # 让 task 挂到 put 上
            task.cancel()
            try:
                await task
                return "returned"
            except asyncio.CancelledError:
                return "cancelled"

        self.assertIn(asyncio.run(scenario()), {"returned", "cancelled"})


class SentinelDeadlockRegressionTests(unittest.TestCase):
    """复现修复前的死锁条件: 慢消费者 + 满队列 + runner 结束投哨兵."""

    def test_consumer_exits_after_drain_with_slow_client(self) -> None:
        """慢消费者逐条排空后必须能等到哨兵并退出, 而不是永久挂起."""

        async def scenario() -> int:
            q: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
            set_sink(q, block_on_full=True)
            sentinel = object()
            produced = 0

            async def runner() -> None:
                nonlocal produced
                try:
                    for i in range(20):  # 远大于 maxsize, 强制背压
                        await emit({"type": "step_token", "content": i})
                        produced += 1
                finally:
                    # 与 rag_service._runner 相同的收尾协议
                    await put_bounded(q, {"__sentinel__": sentinel})
                    return

            runner_task = asyncio.create_task(runner())
            tokens = 0
            while True:
                ev = await q.get()
                if ev.get("__sentinel__") is sentinel:
                    break
                tokens += 1
                await asyncio.sleep(0)  # 模拟慢客户端逐条消费
            try:
                await runner_task
            except (asyncio.CancelledError, Exception):
                pass
            return tokens

        # 修复前: 哨兵被满队列吞掉, 这里会永久挂起导致测试超时
        self.assertEqual(asyncio.run(scenario()), 20)


if __name__ == "__main__":
    unittest.main()
