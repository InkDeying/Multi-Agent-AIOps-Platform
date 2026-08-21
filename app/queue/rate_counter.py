"""Redis 固定窗口限流计数.

本模块只负责 Redis 计数和限流判断，不依赖 FastAPI。
HTTP 请求对象、429 异常和调用方身份解析位于 ``app.api.rate_limit``。
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from app.config import settings


async def _redis() -> Any | None:
    if not settings.rate_limit_enabled:
        return None
    try:
        from app.queue.redis_streams import incident_queue

        return await incident_queue.client()
    except Exception as exc:  # pragma: no cover
        logger.warning(
            f"[ratelimit] Redis 不可达, 限流降级放行: {type(exc).__name__}: {exc}"
        )
        return None


async def hit(
    scope: str,
    identity: str,
    limit: int,
    window_sec: int,
) -> tuple[bool, int]:
    """记一次访问，返回 ``(是否放行, retry_after 秒)``."""
    client = await _redis()
    if client is None or limit <= 0:
        return True, 0
    now = int(time.time())
    bucket = now // window_sec
    key = f"rate_limit:{scope}:{identity}:{bucket}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_sec)
        if int(count) > limit:
            retry_after = window_sec - (now % window_sec)
            return False, max(1, retry_after)
        return True, 0
    except Exception as exc:
        logger.warning(
            f"[ratelimit] 计数失败, 放行 scope={scope}: "
            f"{type(exc).__name__}: {exc}"
        )
        return True, 0
