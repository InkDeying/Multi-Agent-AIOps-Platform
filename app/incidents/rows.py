"""alerts / incident_groups / diagnosis_tasks 三张表的行解码.

这些表的部分列混放 JSON 与纯文本 (payload 里可能是对象, error 是纯字符串),
所以必须用 ``strict_prefix``: 只有看起来像 JSON 对象/数组的字符串才解码。
"""

from __future__ import annotations

from typing import Any

from app.db.base import row_to_dict, rows_to_dicts

JSON_COLUMNS = (
    "labels",
    "annotations",
    "raw_payload",
    "metadata",
    "payload",
    "content",
    "evidence_ids",
)



def record_to_dict(record: Any | None) -> dict[str, Any] | None:
    return row_to_dict(record, JSON_COLUMNS, strict_prefix=True)



def records_to_dicts(records: Any) -> list[dict[str, Any]]:
    return rows_to_dicts(records, JSON_COLUMNS, strict_prefix=True)
