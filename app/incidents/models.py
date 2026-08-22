"""Pydantic contracts for the Incident pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class IncidentStatus(StrEnum):
    OPEN = "open"
    MITIGATED = "mitigated"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"


class DiagnosisMode(StrEnum):
    FAST = "fast"
    DEEP = "deep"


class DiagnosisTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NormalizedAlert(BaseModel):
    """Canonical alert shape stored before correlation."""

    id: str
    idempotency_key: str
    fingerprint: str
    status: AlertStatus = AlertStatus.FIRING
    alertname: str
    severity: str = "warning"
    service: str = ""
    instance: str = ""
    receiver: str = ""
    group_key: str = ""
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    query: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class IncidentIngestResult(BaseModel):
    """Result returned by the ingestion + correlation layer."""

    alert_id: str
    incident_group_id: str
    incident_id: str
    correlation_key: str
    task_id: str
    task_created: bool
    needs_enqueue: bool = False
    """新任务, 或复用了 "pending 且从未成功入队" 的任务 —— 调用方应尝试投递队列.

    复用 running 任务 / 已带队列消息的 pending 任务时为 False, 避免重复投递。
    """


class DiagnosisTaskRecord(BaseModel):
    id: str
    incident_group_id: str
    incident_id: str
    status: DiagnosisTaskStatus
    priority: int = 100
    diagnosis_mode: DiagnosisMode = DiagnosisMode.FAST
    queue_message_id: str = ""
    attempts: int = 0
    max_attempts: int = 3
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    claimed_at: datetime | None = None
    finished_at: datetime | None = None
