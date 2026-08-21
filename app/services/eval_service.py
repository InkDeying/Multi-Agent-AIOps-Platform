"""RAG 评测报告只读查询服务."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parents[2] / "benchmark" / "reports"
MERGED_REPORTS_FILE = REPORTS_DIR / "merged_reports.json"
_FILENAME_RE = re.compile(r"^(?P<mode>[a-z_]+)_(?P<ts>\d{8}-\d{6})\.json$")


def list_reports(limit: int, mode: str | None) -> dict[str, Any]:
    if not REPORTS_DIR.exists():
        return {
            "count": 0,
            "items": [],
            "reports_dir": str(REPORTS_DIR),
            "note": "目录不存在, 先运行 benchmark/run_benchmark.py",
        }

    payloads = _load_merged_reports()
    for path in REPORTS_DIR.glob("*.json"):
        if path.name == MERGED_REPORTS_FILE.name or not _FILENAME_RE.match(path.name):
            continue
        try:
            payloads[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

    items: list[dict[str, Any]] = []
    for name in sorted(payloads, reverse=True):
        match = _FILENAME_RE.match(name)
        if not match:
            continue
        file_mode = match.group("mode")
        if mode and file_mode != mode:
            continue
        payload = payloads[name]
        items.append(
            {
                "name": name,
                "mode": file_mode,
                "generated_at": _parse_ts(match.group("ts")),
                "size_bytes": len(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                ),
                "summary": _summarize(payload),
            }
        )
        if len(items) >= limit:
            break
    return {"count": len(items), "items": items, "reports_dir": str(REPORTS_DIR)}


def get_report(name: str, include_details: bool) -> dict[str, Any]:
    payload = _load_report(name)
    if include_details:
        return payload
    light = {key: value for key, value in payload.items() if key != "details"}
    light["details_count"] = len(payload.get("details") or [])
    return light


def list_low_scores(
    name: str,
    threshold: float,
    metric: str,
    limit: int,
) -> dict[str, Any]:
    payload = _load_report(name)
    details = payload.get("details") or []
    mode = payload.get("mode")
    out: list[dict[str, Any]] = []
    if mode == "ragas":
        for row in details:
            score = (row.get("scores") or {}).get(metric)
            if score is not None and score <= threshold:
                out.append(
                    {
                        "id": row.get("id"),
                        "scenario": row.get("scenario"),
                        "question": row.get("question"),
                        "answer": (row.get("answer") or "")[:300],
                        "score": score,
                        "all_scores": row.get("scores"),
                    }
                )
    elif mode == "retrieval":
        for row in details:
            score = (row.get("score") or {}).get("hit", 0.0)
            if score < 0.5:
                out.append(
                    {
                        "id": row.get("id"),
                        "scenario": row.get("scenario"),
                        "query": row.get("query"),
                        "score": row.get("score"),
                        "hits_top": (row.get("hits") or [])[:3],
                    }
                )
    out.sort(
        key=lambda item: (
            item.get("score")
            if isinstance(item.get("score"), (int, float))
            else 0.0
        )
    )
    return {
        "mode": mode,
        "metric": metric if mode == "ragas" else "hit",
        "threshold": threshold,
        "count": len(out),
        "items": out[:limit],
    }


def _load_report(name: str) -> dict[str, Any]:
    _validate_filename(name)
    path = REPORTS_DIR / name
    if path.is_file() and name != MERGED_REPORTS_FILE.name:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"读取失败: {exc}") from exc
    payload = _load_merged_reports().get(name)
    if payload is None:
        raise FileNotFoundError("report not found")
    return payload


def _load_merged_reports() -> dict[str, dict[str, Any]]:
    if not MERGED_REPORTS_FILE.is_file():
        return {}
    try:
        merged = json.loads(MERGED_REPORTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    reports: dict[str, dict[str, Any]] = {}
    for item in merged.get("reports") or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source") or {}
        name = str(source.get("file") or "")
        payload = item.get("data")
        if _FILENAME_RE.match(name) and isinstance(payload, dict):
            reports[name] = payload
    return reports


def _validate_filename(name: str) -> None:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("非法文件名")


def _parse_ts(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y%m%d-%H%M%S").isoformat()
    except Exception:
        return ts


def _summarize(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode", "")
    summary: dict[str, Any] = {
        "mode": mode,
        "rows": payload.get("rows"),
        "elapsed_sec": payload.get("elapsed_sec"),
    }
    if mode == "retrieval":
        summary.update(
            {
                "k": payload.get("k"),
                "hit_at_k": payload.get("hit_at_k"),
                "mrr_at_k": payload.get("mrr_at_k"),
                "recall_at_k": payload.get("recall_at_k"),
                "hybrid": payload.get("hybrid"),
                "rerank": payload.get("rerank"),
            }
        )
    elif mode == "ragas":
        averages = payload.get("averages") or {}
        openevals = payload.get("openevals_averages") or {}
        summary.update(
            {
                "faithfulness": averages.get("faithfulness"),
                "answer_relevancy": averages.get("answer_relevancy"),
                "context_precision": averages.get("context_precision"),
                "context_recall": averages.get("context_recall"),
                "groundedness": openevals.get("groundedness"),
                "helpfulness": openevals.get("helpfulness"),
            }
        )
    return summary
