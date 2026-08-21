"""审批请求用例服务.

Postgres 仓储位于 ``app.db.approvals``；API 不直接接触存储实现。
"""

from __future__ import annotations

from typing import Any

from app.db.approvals import approval_repository


async def list_pending(limit: int = 50) -> dict[str, Any]:
    try:
        items = await approval_repository.list_pending(limit=limit)
    except Exception as exc:
        return {"count": 0, "items": [], "available": False, "error": str(exc)}
    return {"count": len(items), "items": items, "available": True}


async def list_recent(limit: int = 50) -> dict[str, Any]:
    try:
        items = await approval_repository.list_recent(limit=limit)
    except Exception as exc:
        return {"count": 0, "items": [], "available": False, "error": str(exc)}
    return {"count": len(items), "items": items, "available": True}


async def get_one(req_id: str) -> dict[str, Any] | None:
    return await approval_repository.get_request(req_id)


async def decide(
    req_id: str,
    *,
    decision: str,
    decided_by: str = "",
    reason: str = "",
) -> dict[str, Any] | None:
    return await approval_repository.decide(
        req_id,
        decision=decision,
        decided_by=decided_by,
        reason=reason,
    )
