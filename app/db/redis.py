"""Redis 连接原语.

和 ``app/db/postgres.py`` 对称: 这里只负责"把 redis.asyncio 客户端建出来并
确认连得上", 不做任何降级决策。

为什么要抽出来: 之前 ``queue/redis_streams.py``、``db/rag_chat_memory.py``
各自写了一遍 "try import redis → from_url → ping" 的开场, 连接参数散在两处,
出问题时不好对齐。降级策略仍然留在各调用点, 因为三者本来就不一样:
  - 队列: 连不上必须启动失败 (硬依赖);
  - 会话记忆: 连不上静默降级为无记忆;
  - 限流器: 连不上 fail-open 放行。
"""

from __future__ import annotations

from typing import Any


async def open_client(url: str, **options: Any) -> Any:
    """建立一个 redis.asyncio 客户端并 ping 通.

    任何失败都直接向上抛 (包括 redis 包缺失时的 ``ImportError``),
    由调用方按自己的容错策略处理。
    """
    from redis.asyncio import Redis

    client = Redis.from_url(url, **options)
    await client.ping()
    return client
