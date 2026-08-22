"""LLM Wiki 的纯文本工具.

本模块不读 settings、不碰磁盘, 只做确定性转换, 供 ingest / recall / lint 共用。

其中"文件行格式协议"小节是 log.md / index.md 行格式的**单一定义处**: 写端
(store.py) 和读端 (wiki_service.py) 必须引用这里的函数, 不允许各自硬编码 ——
两端独立复刻格式会在变更时静默漂移 (读端跳过不认识的行, 不报错)。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-z0-9_]{2,}")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def slug(text: Any, fallback: str = "unknown") -> str:
    """把任意文本压成最多 64 字符的 Wiki 文件名片段."""
    value = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return (value or fallback)[:64]


def tokenize(text: Any) -> set[str]:
    """英文按长度至少 2 的单词切分, 中文按单字切分, 用于轻量目录召回."""
    value = str(text or "").lower()
    return set(_WORD.findall(value)) | set(_CJK.findall(value))


def coerce_text(content: Any) -> str:
    """把 LLM 消息的 str / list[dict|str] content 转成纯文本.

    与 ``harness.core.llm_parse.content_to_text`` 的语义刻意不同:
    Wiki 合并还接受 block 的 ``content`` 键, 非 str/list 的 falsy 值统一转空串。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "".join(parts)
    return str(content or "")


def parse_target(signature: str, query: str) -> tuple[str, str]:
    """从 alert_signature (``alertname|service``) 解析 service 与 pattern slug.

    手动诊断无 signature 时, service 留空、pattern 用 query 关键词派生。
    """
    service = ""
    if signature and "|" in signature:
        service = signature.split("|", 1)[1].strip()
    pattern_slug = slug(signature or query, fallback="incident")
    return service, pattern_slug


# ==================== 文件行格式协议 (单一定义处) ====================

LOG_LINE_RE = re.compile(r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\]\s*(?P<body>.*)$")
INDEX_LINE_PREFIX = "- [["


def wiki_today() -> str:
    """Wiki 流水使用的 UTC 日期 (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def format_log_line(entry: Any, *, date: str | None = None) -> str:
    """渲染 log.md 的一行: ``## [YYYY-MM-DD] 单行 entry`` (空白折叠为单行)."""
    body = " ".join(str(entry or "").split())
    return f"## [{date or wiki_today()}] {body}"


def parse_log_line(line: str) -> dict[str, str] | None:
    """解析 log.md 的一行; 非流水行返回 None, 由调用方自行跳过."""
    match = LOG_LINE_RE.match(str(line or "").strip())
    if not match:
        return None
    return {"date": match.group("date"), "entry": match.group("body").strip()}


def format_index_line(ref: str, summary: Any) -> str:
    """渲染 index.md 的目录行: ``- [[ref]] — summary`` (summary 截断 100 字符)."""
    body = " ".join(str(summary or "").split())[:100] or ref
    return f"- [[{ref}]] — {body}"


def is_index_line(line: str) -> bool:
    """判断 index.md 的一行是否为目录行 (写端合并时用来过滤其他行)."""
    return str(line or "").startswith(INDEX_LINE_PREFIX)
