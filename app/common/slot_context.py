"""跨层分布式槽位上下文。

这里只保存当前协程的槽位句柄，不实现 Redis 或并发策略，供 Queue 设置、Runtime
在等待审批时暂时释放并恢复槽位。
"""

from contextvars import ContextVar
from typing import Any

current_slot: ContextVar[Any | None] = ContextVar("current_slot", default=None)
