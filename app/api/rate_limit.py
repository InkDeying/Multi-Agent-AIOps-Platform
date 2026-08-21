"""HTTP 层限流适配.

Redis 计数实现位于 ``app.queue.rate_counter``；本模块只处理 Request 身份、
429 响应和 API 调用所需的异常映射。
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from loguru import logger

from app.queue.rate_counter import hit


def client_ip(request: Request) -> str:
    """取调用方 IP，优先 X-Forwarded-For，回落 request.client."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _raise_429(
    retry_after: int,
    detail: str = "请求过于频繁，请稍后再试",
) -> None:
    raise HTTPException(
        status_code=429,
        detail={
            "error": "rate_limited",
            "message": detail,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


async def enforce(
    scope: str,
    identity: str,
    limit: int,
    window_sec: int,
    detail: Optional[str] = None,
) -> None:
    """检查限流并在超限时抛出 HTTP 429."""
    ok, retry_after = await hit(scope, identity, limit, window_sec)
    if not ok:
        logger.info(
            f"[ratelimit] blocked scope={scope} "
            f"id={identity} limit={limit}/{window_sec}s"
        )
        _raise_429(retry_after, detail or "请求过于频繁，请稍后再试")
