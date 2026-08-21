"""Alertmanager 告警的归一化与关联策略 (纯函数, 不碰数据库).

这一层原先长在 ``repository.py`` 里, 但它决定的是"两条告警算不算同一次故障"
—— 属于领域策略, 不属于持久化。抽出来之后可以单独读、单独测, 也不用再为了
看一眼关联口径去翻 800 行 SQL。

关联口径:
  - Alertmanager 给了 groupKey 就直接信它 (``alertmanager:{receiver}:{groupKey}``);
  - 否则按 cluster + namespace + service + 时间桶 归并 (``window:...``),
    时间桶大小由 ``settings.incident_time_bucket_sec`` 决定。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.incidents.models import AlertStatus, NormalizedAlert

def stable_id(prefix: str, value: str, length: int = 24) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"



def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def time_bucket(dt: datetime | None) -> int:
    base = dt or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    bucket = max(1, settings.incident_time_bucket_sec)
    return int(base.timestamp()) // bucket * bucket


def extract_service(labels: dict[str, Any]) -> str:
    for key in ("service", "service_name", "app", "job", "container", "pod"):
        value = labels.get(key)
        if value:
            return str(value)
    return ""


def normalize_alert(payload: Any, alert: Any, query: str) -> NormalizedAlert:
    labels = dict(getattr(alert, "labels", {}) or {})
    annotations = dict(getattr(alert, "annotations", {}) or {})
    alertname = str(labels.get("alertname") or "UnknownAlert")
    instance = str(labels.get("instance") or "")
    receiver = str(getattr(payload, "receiver", "") or "")
    group_key = str(getattr(payload, "groupKey", "") or "")
    starts_at = parse_datetime(getattr(alert, "startsAt", "") or None)
    ends_at = parse_datetime(getattr(alert, "endsAt", "") or None)
    fingerprint = str(getattr(alert, "fingerprint", "") or "")
    if not fingerprint:
        fingerprint = stable_id(
            "fp",
            f"{alertname}:{instance}:{starts_at.isoformat() if starts_at else ''}",
            length=16,
        )

    bucket = time_bucket(starts_at)
    idempotency_key = f"{receiver}:{group_key}:{fingerprint}:{bucket}:{getattr(alert, 'status', 'firing')}"
    alert_id = stable_id("al", idempotency_key)

    raw_payload = {
        "externalURL": getattr(payload, "externalURL", ""),
        "groupLabels": getattr(payload, "groupLabels", {}) or {},
        "commonLabels": getattr(payload, "commonLabels", {}) or {},
        "commonAnnotations": getattr(payload, "commonAnnotations", {}) or {},
        "generatorURL": getattr(alert, "generatorURL", ""),
        "truncatedAlerts": getattr(payload, "truncatedAlerts", 0),
    }

    return NormalizedAlert(
        id=alert_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        status=AlertStatus(getattr(alert, "status", "firing") or "firing"),
        alertname=alertname,
        severity=str(labels.get("severity") or "warning"),
        service=extract_service(labels),
        instance=instance,
        receiver=receiver,
        group_key=group_key,
        labels=labels,
        annotations=annotations,
        raw_payload=raw_payload,
        query=query,
        starts_at=starts_at,
        ends_at=ends_at,
    )

def correlation_key(payload: Any, alert: NormalizedAlert) -> str:
    if alert.group_key:
        return f"alertmanager:{alert.receiver}:{alert.group_key}"
    labels = alert.labels
    cluster = labels.get("cluster") or labels.get("cluster_name") or ""
    namespace = labels.get("namespace") or labels.get("kubernetes_namespace") or ""
    service = alert.service or alert.instance or "unknown"
    bucket = time_bucket(alert.starts_at)
    return f"window:{cluster}:{namespace}:{service}:{bucket}"


def summary_for(payload: Any, alert: NormalizedAlert) -> str:
    common = getattr(payload, "commonAnnotations", {}) or {}
    return (
        str(common.get("summary") or "")
        or str(alert.annotations.get("summary") or "")
        or str(alert.annotations.get("description") or "")
        or f"{alert.alertname} on {alert.service or alert.instance or 'unknown'}"
    )
