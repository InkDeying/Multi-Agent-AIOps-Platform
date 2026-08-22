"""Evidence Store contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field



class EvidenceSource(StrEnum):
    """证据在诊断证据链中的来源分类。字符串值属于持久化协议。"""

    ALERT = "alert"
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    RUNBOOK = "runbook"
    INCIDENT_HISTORY = "incident_history"
    RCA = "rca"
    MCP_TOOL_RESULT = "mcp_tool_result"
    HUMAN_FEEDBACK = "human_feedback"


class EvidenceCreate(BaseModel):
    incident_group_id: str
    incident_id: str | None = None
    source: EvidenceSource | str
    type: str
    summary: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
