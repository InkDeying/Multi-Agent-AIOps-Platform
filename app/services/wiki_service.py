"""LLM Wiki 只读查询服务."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.wiki_storage import WIKI_DIR
from app.harness.wiki.text_utils import parse_log_line

_CATEGORIES = ("services", "patterns")


def overview() -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": bool(settings.wiki_enabled),
        "recall_enabled": bool(settings.wiki_recall_enabled),
        "wiki_dir": str(WIKI_DIR),
        "exists": WIKI_DIR.exists(),
        "pages": {},
    }
    if not WIKI_DIR.exists():
        return out
    for category in _CATEGORIES:
        directory = WIKI_DIR / category
        out["pages"][category] = (
            sum(1 for _ in directory.glob("*.md")) if directory.exists() else 0
        )
    out["index_exists"] = (WIKI_DIR / "index.md").exists()
    out["log_exists"] = (WIKI_DIR / "log.md").exists()
    return out


def list_pages(category: str | None, limit: int) -> dict[str, Any]:
    if not WIKI_DIR.exists():
        return {"count": 0, "items": []}
    categories = [category] if category else list(_CATEGORIES)
    items: list[dict[str, Any]] = []
    for current in categories:
        if current not in _CATEGORIES:
            continue
        directory = WIKI_DIR / current
        if not directory.exists():
            continue
        for path in directory.glob("*.md"):
            meta = _stat(path)
            preview = ""
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    text = line.strip()
                    if text:
                        preview = text.lstrip("#").strip()[:160]
                        break
            except Exception:
                pass
            items.append(
                {
                    "category": current,
                    "slug": path.stem,
                    "ref": f"{current}/{path.stem}",
                    "preview": preview,
                    **meta,
                }
            )
    items.sort(key=lambda item: item.get("modified_at") or "", reverse=True)
    return {"count": len(items), "items": items[:limit]}


def get_page(category: str, slug: str) -> dict[str, Any]:
    path = safe_page_path(category, slug)
    if not path.is_file():
        raise FileNotFoundError("page not found")
    return {
        "category": category,
        "slug": slug,
        "ref": f"{category}/{slug}",
        "content": path.read_text(encoding="utf-8"),
        **_stat(path),
    }


def get_index() -> dict[str, Any]:
    path = WIKI_DIR / "index.md"
    if not path.exists():
        return {"content": "", "exists": False}
    return {
        "exists": True,
        "content": path.read_text(encoding="utf-8"),
        **_stat(path),
    }


def get_log(limit: int) -> dict[str, Any]:
    path = WIKI_DIR / "log.md"
    if not path.exists():
        return {"count": 0, "items": []}
    entries: list[dict[str, Any]] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        parsed = parse_log_line(line)
        if parsed is None:
            continue
        entries.append(parsed)
        if len(entries) >= limit:
            break
    return {"count": len(entries), "items": entries}


def safe_page_path(category: str, slug: str) -> Path:
    if category not in _CATEGORIES:
        raise ValueError("非法 category, 仅支持 services / patterns")
    if not re.fullmatch(r"[a-z0-9_\-]{1,80}", slug or ""):
        raise ValueError("非法 slug")
    path = (WIKI_DIR / category / f"{slug}.md").resolve()
    base = (WIKI_DIR / category).resolve()
    if base not in path.parents:
        raise ValueError("路径越界")
    return path


def _stat(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    except Exception:
        return {"size_bytes": 0, "modified_at": None}
