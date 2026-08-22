"""Executor 流式事件旁路.

LangGraph graph.astream() 只产出节点级事件, Executor 内部 LLM 的 token 级流式
没有官方出口. 这里用 ContextVar + asyncio.Queue 把 token 从 tool_runner
外送给 diagnosis_runner, 让前端/Worker 能看到 Executor 正在生成文字并记录工具事件.

用法:
  - diagnosis_runner 在启动 graph.astream 前调 set_sink(queue), 然后把 graph.astream
    包成 Task (Task 自动复制当前 context, 所以 tool_runner 里 get_sink() 拿得到).
  - tool_runner 每次流式输出调 await emit({...}).
  - diagnosis_runner 主循环 merge 自己的 "node event" 和 queue 里的 "token event",
    统一 yield 给 SSE.

为什么放 app/harness/runtime/ 而不是 app/agents/:
  本质是底层"事件总线"工具, 与 transitions / agent_harness 同层。原先放 agents/
  会和 runtime/tool_runner.py 互相 import, 形成 runtime <-> agents 真循环。
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Dict, Optional

_sink_var: ContextVar[Optional["asyncio.Queue[Dict[str, Any]]"]] = ContextVar(
    "executor_stream_sink", default=None
)
# 无损模式: 满队列时 emit 阻塞等待空位而不是丢弃事件。
# 依赖消费方在退出路径 (含 GeneratorExit) 取消生产侧 task, 否则生产者会滞留。
_sink_block_var: ContextVar[bool] = ContextVar(
    "executor_stream_sink_block", default=False
)
_step_var: ContextVar[int] = ContextVar("executor_current_step", default=0)


def set_sink(
    queue: "asyncio.Queue[Dict[str, Any]]",
    *,
    block_on_full: bool = False,
) -> None:
    """登记当前流的中转队列。

    block_on_full=False (默认, 诊断流): 满队列丢弃事件, 生产者永不阻塞;
    block_on_full=True (RAG 聊天流): 满队列阻塞等待, token 不丢失,
    输出速度由消费方 (SSE 客户端) 反压决定。
    """
    _sink_var.set(queue)
    _sink_block_var.set(block_on_full)


def set_step(iteration: int) -> None:
    _step_var.set(iteration)


def get_step() -> int:
    return _step_var.get()


_miss_count = 0
_emit_count = 0


async def emit(event: Dict[str, Any]) -> None:
    """把事件推到消费方的中转队列.

    默认模式: 队列不存在或满了, 静默丢弃。
    无损模式 (block_on_full): 满队列阻塞等待空位, 事件不丢。
    """
    global _miss_count, _emit_count
    from loguru import logger  # 放函数内避免循环 import

    q = _sink_var.get()
    if q is None:
        _miss_count += 1
        # 前 5 次 + 每 20 次打一条, 避免日志爆炸
        if _miss_count <= 5 or _miss_count % 20 == 0:
            logger.warning(
                f"[stream_sink] ⚠ sink=None (miss #{_miss_count}) type={event.get('type')} "
                f"— ContextVar 没跨 LangGraph 任务传过来"
            )
        return
    event.setdefault("iteration", _step_var.get())
    if _sink_block_var.get():
        await q.put(event)
        _emit_count += 1
        return
    try:
        q.put_nowait(event)
        _emit_count += 1
        if _emit_count <= 3 or _emit_count % 50 == 0:
            logger.info(
                f"[stream_sink] emit #{_emit_count} type={event.get('type')} "
                f"iter={event.get('iteration')} qsize={q.qsize()}"
            )
    except asyncio.QueueFull:
        logger.warning(f"[stream_sink] queue full, drop type={event.get('type')}")


async def put_bounded(
    queue: "asyncio.Queue[Dict[str, Any]]",
    event: Dict[str, Any],
    *,
    timeout_sec: float = 5.0,
) -> bool:
    """投递"必须送达否则放弃"的事件 (结束哨兵), 返回是否成功。

    为什么不直接 await queue.put(...):
    - 正常路径 (消费方还在排空) 队列很快腾出空位, 阻塞 put 等价于立即成功;
    - 消费方已退出/取消时 (客户端断开), 没有人会再排空队列, 阻塞 put 会
      把生产侧 task 永久卡在 finally 里 —— 这正是要修的挂起缺陷。
    有界等待 + 超时放弃在这里是安全的: 超时意味着没有消费者在等这个哨兵。
    """
    try:
        await asyncio.wait_for(queue.put(event), timeout=timeout_sec)
        return True
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return False
