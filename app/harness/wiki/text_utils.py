"""LLM Wiki 的纯文本工具.

本模块不读 settings、不碰磁盘, 只做确定性转换, 供 ingest / recall / lint 共用。
"""

from __future__ import annotations

import re
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
