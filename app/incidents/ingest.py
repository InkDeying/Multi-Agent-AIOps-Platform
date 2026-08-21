"""两条建单入口: Alertmanager 告警入库, 以及手动升级建单.

两者写的是同一组事实表 (alerts -> incident_groups -> incidents ->
incident_group_alerts -> diagnosis_tasks), 所以放在一起; 但故意不合并成一个方法:
Alertmanager 的 payload 带 receiver/groupKey/labels, 手动场景没有这些字段,
为了复用去伪造 payload 会让 fingerprint 变得不稳定。

``diagnosis_tasks`` 的写入委托给 ``TaskStoreMixin._create_or_get_task``。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.base import acquire
from app.db.base import json_dump
from app.incidents.models import (
    AlertStatus,
    DiagnosisMode,
    IncidentIngestResult,
    NormalizedAlert,
)
from app.incidents.normalizer import (
    correlation_key as build_correlation_key,
    normalize_alert,
    stable_id,
    summary_for,
)


class IngestMixin:
    """把一条告警 (或一次手动升级) 落成完整的事实链."""

    async def ingest_alertmanager_alert(
        self,
        *,
        payload: Any,
        alert: Any,
        query: str,
        diagnosis_mode: DiagnosisMode = DiagnosisMode.FAST,
        priority: int = 100,
    ) -> IncidentIngestResult:
        """Persist alert, correlate it, and create a diagnosis task when needed."""
        normalized = normalize_alert(payload, alert, query)
        correlation_key = build_correlation_key(payload, normalized)
        incident_group_id = stable_id("ig", correlation_key)
        incident_id = stable_id("inc", incident_group_id)
        labels = dict(getattr(payload, "commonLabels", {}) or {})
        labels.update(normalized.labels)
        metadata = {
            "receiver": normalized.receiver,
            "group_key": normalized.group_key,
            "external_url": getattr(payload, "externalURL", ""),
            "source": "alertmanager",
        }

        async with acquire() as conn:
            async with conn.transaction():
                await self._upsert_alert(conn, normalized)
                await self._upsert_incident_group(
                    conn,
                    incident_group_id=incident_group_id,
                    correlation_key=correlation_key,
                    alert=normalized,
                    summary=summary_for(payload, normalized),
                    labels=labels,
                    metadata=metadata,
                )
                await self._upsert_incident(
                    conn,
                    incident_id=incident_id,
                    incident_group_id=incident_group_id,
                    alert=normalized,
                    title=summary_for(payload, normalized),
                    metadata=metadata,
                )
                await conn.execute(
                    """
                    INSERT INTO incident_group_alerts (incident_group_id, alert_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    incident_group_id,
                    normalized.id,
                )
                await conn.execute(
                    """
                    UPDATE incident_groups
                    SET alert_count = (
                        SELECT count(*) FROM incident_group_alerts
                        WHERE incident_group_id = $1
                    ),
                    updated_at = now()
                    WHERE id = $1
                    """,
                    incident_group_id,
                )
                task_id, task_created = await self._create_or_get_task(
                    conn,
                    incident_group_id=incident_group_id,
                    incident_id=incident_id,
                    query=query,
                    alert=normalized,
                    diagnosis_mode=diagnosis_mode,
                    priority=priority,
                )

        return IncidentIngestResult(
            alert_id=normalized.id,
            incident_group_id=incident_group_id,
            incident_id=incident_id,
            correlation_key=correlation_key,
            task_id=task_id,
            task_created=task_created,
        )

    async def _upsert_alert(self, conn: Any, alert: NormalizedAlert) -> None:
        await conn.execute(
            """
            INSERT INTO alerts (
                id, idempotency_key, fingerprint, status, alertname, severity,
                service, instance, receiver, group_key, labels, annotations,
                raw_payload, query, starts_at, ends_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11::jsonb, $12::jsonb,
                $13::jsonb, $14, $15, $16
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                status = EXCLUDED.status,
                labels = EXCLUDED.labels,
                annotations = EXCLUDED.annotations,
                raw_payload = EXCLUDED.raw_payload,
                query = EXCLUDED.query,
                ends_at = EXCLUDED.ends_at,
                last_seen = now(),
                seen_count = alerts.seen_count + 1
            """,
            alert.id,
            alert.idempotency_key,
            alert.fingerprint,
            alert.status.value,
            alert.alertname,
            alert.severity,
            alert.service,
            alert.instance,
            alert.receiver,
            alert.group_key,
            json_dump(alert.labels),
            json_dump(alert.annotations),
            json_dump(alert.raw_payload),
            alert.query,
            alert.starts_at,
            alert.ends_at,
        )


    async def _upsert_incident_group(
        self,
        conn: Any,
        *,
        incident_group_id: str,
        correlation_key: str,
        alert: NormalizedAlert,
        summary: str,
        labels: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO incident_groups (
                id, correlation_key, status, severity, primary_service,
                summary, labels, metadata
            )
            VALUES ($1, $2, 'open', $3, $4, $5, $6::jsonb, $7::jsonb)
            ON CONFLICT (correlation_key) DO UPDATE SET
                severity = EXCLUDED.severity,
                primary_service = COALESCE(NULLIF(incident_groups.primary_service, ''), EXCLUDED.primary_service),
                summary = COALESCE(NULLIF(incident_groups.summary, ''), EXCLUDED.summary),
                labels = incident_groups.labels || EXCLUDED.labels,
                metadata = incident_groups.metadata || EXCLUDED.metadata,
                updated_at = now()
            """,
            incident_group_id,
            correlation_key,
            alert.severity,
            alert.service,
            summary,
            json_dump(labels),
            json_dump(metadata),
        )


    async def _upsert_incident(
        self,
        conn: Any,
        *,
        incident_id: str,
        incident_group_id: str,
        alert: NormalizedAlert,
        title: str,
        metadata: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO incidents (
                id, incident_group_id, status, title, severity, service,
                started_at, metadata
            )
            VALUES ($1, $2, 'open', $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                title = COALESCE(NULLIF(incidents.title, ''), EXCLUDED.title),
                severity = EXCLUDED.severity,
                service = COALESCE(NULLIF(incidents.service, ''), EXCLUDED.service),
                metadata = incidents.metadata || EXCLUDED.metadata,
                updated_at = now()
            """,
            incident_id,
            incident_group_id,
            title,
            alert.severity,
            alert.service,
            alert.starts_at,
            json_dump(metadata),
        )


    async def create_manual_task(
        self,
        *,
        source: str,
        title: str,
        query: str,
        severity: str = "warning",
        service: str = "",
        diagnosis_mode: DiagnosisMode = DiagnosisMode.FAST,
        priority: int = 100,
        context: dict[str, Any] | None = None,
    ) -> IncidentIngestResult:
        """无 Alertmanager 上下文的手动建任务 (聊天升级 / 命令行触发 / 外部对接).

        和 ingest_alertmanager_alert 共享同一张事实表, 让"手动诊断"也能在事件中心、
        证据链、Wiki ingest 里被统一看到, 而不是漂在另一条隐形通道上.

        为什么不复用 ingest_alertmanager_alert: AM payload 太重 (receiver/groupKey/labels),
        手动场景没有这些字段; 构造一个假 payload 会引入 fingerprint 不稳定的副作用.
        """
        now = datetime.now(timezone.utc)
        correlation_key = f"manual:{source}:{stable_id('mc', f'{source}|{title}|{query[:200]}', length=16)}"
        incident_group_id = stable_id("ig", correlation_key)
        incident_id = stable_id("inc", incident_group_id)

        # 合成一条 NormalizedAlert 占位 (用于复用 _upsert_alert 的 schema)
        synthetic_alertname = (title or query[:80] or "ManualEscalation").strip()
        idempotency_key = f"manual:{source}:{stable_id('ak', f'{title}|{query[:200]}', length=24)}"
        fingerprint = stable_id("fp", correlation_key, length=16)
        synth_alert = NormalizedAlert(
            id=stable_id("al", idempotency_key),
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            status=AlertStatus.FIRING,
            alertname=synthetic_alertname,
            severity=severity or "warning",
            service=service or "",
            instance="",
            receiver=source,
            group_key="",
            labels={"source": source, "manual": "true"},
            annotations={"summary": title or query[:200]},
            raw_payload={"source": source, "context": context or {}},
            query=query or title or "",
            starts_at=now,
            ends_at=None,
        )

        summary_text = (title or query or synthetic_alertname).strip()[:500]
        metadata = {
            "source": source,
            "manual": True,
            "context": context or {},
        }

        async with acquire() as conn:
            async with conn.transaction():
                await self._upsert_alert(conn, synth_alert)
                await self._upsert_incident_group(
                    conn,
                    incident_group_id=incident_group_id,
                    correlation_key=correlation_key,
                    alert=synth_alert,
                    summary=summary_text,
                    labels=dict(synth_alert.labels),
                    metadata=metadata,
                )
                await self._upsert_incident(
                    conn,
                    incident_id=incident_id,
                    incident_group_id=incident_group_id,
                    alert=synth_alert,
                    title=summary_text,
                    metadata=metadata,
                )
                await conn.execute(
                    """
                    INSERT INTO incident_group_alerts (incident_group_id, alert_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    incident_group_id,
                    synth_alert.id,
                )
                task_id, task_created = await self._create_or_get_task(
                    conn,
                    incident_group_id=incident_group_id,
                    incident_id=incident_id,
                    query=query or title,
                    alert=synth_alert,
                    diagnosis_mode=diagnosis_mode,
                    priority=priority,
                )

        return IncidentIngestResult(
            alert_id=synth_alert.id,
            incident_group_id=incident_group_id,
            incident_id=incident_id,
            correlation_key=correlation_key,
            task_id=task_id,
            task_created=task_created,
        )
