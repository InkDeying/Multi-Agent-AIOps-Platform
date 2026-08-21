"""Alertmanager webhook 入账接口。

webhook 只做"入账+入队",不跑诊断: 校验 → 归一 → 去重 → 落 Postgres → 投 Redis 队列。
真正的诊断在独立进程 `python -m app.diagnosis_worker` 里跑。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.api import rate_limit
from app.services import incident_service, webhook_service

router = APIRouter(prefix="/webhook", tags=["webhook"])


class AlertmanagerAlert(BaseModel):
    """单条 Alertmanager 告警 (firing 或 resolved)。"""

    status: str = Field(default="firing", description="firing | resolved")
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: str = Field(default="")
    endsAt: str = Field(default="")
    generatorURL: str = Field(default="")
    fingerprint: str = Field(default="")


class AlertmanagerPayload(BaseModel):
    """Alertmanager v4 webhook 完整 payload (一组告警)。"""

    version: str = Field(default="4")
    groupKey: str = Field(default="")
    truncatedAlerts: int = Field(default=0)
    status: str = Field(default="firing")
    receiver: str = Field(default="")
    groupLabels: dict[str, Any] = Field(default_factory=dict)
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    commonAnnotations: dict[str, Any] = Field(default_factory=dict)
    externalURL: str = Field(default="")
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


@router.post(
    "/alertmanager",
    summary="Alertmanager alert ingestion",
    description=(
        "Accept Alertmanager v4 payloads, persist alerts and incident groups, "
        "then enqueue diagnosis tasks to Redis Streams. The request returns quickly; "
        "diagnosis is performed by the worker process."
    ),
)
async def alertmanager_webhook(payload: AlertmanagerPayload, request: Request) -> dict[str, Any]:
    # 限流 (改造文档第 8 步): 单 IP/API Key 每秒 + 单来源(receiver)每分钟, 超限 429.
    # API Key 优先取 X-API-Key 头, 没有则用 IP; source 用 Alertmanager receiver。
    identity = request.headers.get("x-api-key") or rate_limit.client_ip(request)
    await rate_limit.enforce(
        "webhook_key", identity, settings.rate_limit_webhook_per_ip_per_sec, 1,
        detail="告警推送过于频繁 (单源每秒上限)",
    )
    source = str(payload.receiver or "default")
    await rate_limit.enforce(
        "webhook_src", source, settings.rate_limit_webhook_per_source_per_min, 60,
        detail="该来源告警过于频繁 (单源每分钟上限)",
    )

    return await webhook_service.process_alertmanager_payload(payload)


@router.get(
    "/history",
    summary="Recent diagnosis tasks",
    description="Compatibility endpoint: returns recent queued/processed diagnosis tasks.",
)
async def get_history(limit: int = 20) -> dict[str, Any]:
    return await incident_service.list_tasks(limit=limit)


@router.delete(
    "/history",
    summary="Legacy no-op",
    description="The new pipeline stores task history in Postgres; destructive clearing is disabled.",
)
async def clear_history() -> dict[str, Any]:
    return {
        "status": "disabled",
        "message": "Incident history is stored in Postgres and is not cleared by this endpoint.",
    }
