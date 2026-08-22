"""RAG Chat 的联网搜索上下文构造.

策略 (两层准入, 敏感规则最优先):
  - 敏感信息 (IP/密钥/Bearer/手机号/长数字) 与禁止关键词直接拒;
  - Tier 1: 管理员配置的常用词 (RAG_CHAT_WEB_SEARCH_KEYWORDS) 命中即放行,
    原文外发 —— 这是显式运维策略;
  - Tier 2 (white-list by reference): 其余查询的候选术语必须出现在参考语料
    (本会话 assistant 内容 + summary + 最近诊断报告) 中。全部命中原文外发,
    部分命中只外发命中的术语, 无一命中拒绝 —— 防止聊天中冒出的内部实体
    被原文送往外部搜索引擎。

真正的 provider 调度走 app.harness.websearch (app.web_search 只是兼容壳)
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from app.config import settings
import app.db.rag_chat_memory as rag_memory_store
from app.harness.websearch import format_results, get_provider, search

_SENSITIVE_WEB_PATTERNS = (
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\.\-_]+", re.IGNORECASE),
    re.compile(r"\b1\d{10}\b"),
    re.compile(r"\b\d{15,18}\b"),
)

_BLOCKED_WEB_KEYWORDS = (
    "password",
    "passwd",
    "api_key",
    "api-key",
    "token",
    "secret",
    "私钥",
)


_REPORT_TERM_RE = re.compile(
    r"""
    [A-Za-z][A-Za-z0-9_.\-]{1,}
    |
    [A-Za-z0-9_.\-]*\.exe
    |
    [\u4e00-\u9fffA-Za-z0-9_.\-]*(?:进程|服务|程序|工具|容器|镜像|实例|虚拟机|内存|磁盘|CPU)[\u4e00-\u9fffA-Za-z0-9_.\-]*
    """,
    re.IGNORECASE | re.VERBOSE,
)

_REPORT_TERM_STOPWORDS = {
    "什么",
    "怎么",
    "为什么",
    "刚刚",
    "刚才",
    "之前",
    "前面",
    "说的",
    "这个",
    "那个",
    "帮我",
    "一下",
    "可以",
    "联网",
    "搜索",
    "查询",
    "诊断",
    "报告",
    "问题",
    "原因",
    "cpu",
    "gpu",
    "ram",
    "mb",
    "gb",
    "kb",
    "ms",
    "pid",
    "what",
    "why",
    "how",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
}

_WEB_QUERY_PREFIX_RE = re.compile(
    r"^\s*(?:请)?(?:帮我)?(?:联网(?:搜索|查询|查找|看看|看一下|查一下)?|搜索|查询|查找|查一下|看一下)[：:\s，,、-]*",
    re.IGNORECASE,
)


def _normalize_web_query(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip())
    for _ in range(3):
        updated = _WEB_QUERY_PREFIX_RE.sub("", normalized).strip()
        if updated == normalized:
            break
        normalized = updated
    return normalized


def _allowed_web_keywords() -> list[str]:
    return [
        item.strip().lower()
        for item in settings.rag_chat_web_search_keywords.split(",")
        if item.strip()
    ]


def _extract_web_topics(text: str) -> list[str]:
    lower = text.lower()
    topics = []
    for keyword in _allowed_web_keywords():
        pattern = rf"(?<![a-z0-9_\-]){re.escape(keyword)}(?![a-z0-9_\-])"
        if re.search(pattern, lower) and keyword not in topics:
            topics.append(keyword)
    return topics


def _web_query_block_reason(text: str) -> str:
    lower = text.lower()
    for keyword in _BLOCKED_WEB_KEYWORDS:
        if keyword.lower() in lower:
            return f"命中禁止联网关键词: {keyword}"
    for pattern in _SENSITIVE_WEB_PATTERNS:
        if pattern.search(text):
            return "query 含敏感信息"
    return ""


def _extract_report_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in _REPORT_TERM_RE.findall(text or ""):
        term = raw.strip("`'\"“”‘’,。！？；：:;,.()()[]【】<>《》")
        if not term:
            continue
        lower = term.lower()
        if len(lower) < 2 or lower in _REPORT_TERM_STOPWORDS:
            continue
        if lower.isdigit():
            continue
        terms.add(lower)
    return terms


def _reference_corpus(
    *,
    summary: str,
    recent_messages: list[dict[str, Any]],
    extra_reports: list[str] | None,
) -> str:
    """拼出 "历史诊断报告" 参考语料: 本会话 assistant 内容 + summary + 额外报告.

    只取 assistant 角色: 用户自己说过的话不构成 "诊断报告出现过的实体",
    否则用户提任何内部实体都会自动进入白名单, 白名单就失去意义。
    """
    parts: list[str] = []
    for item in recent_messages or []:
        if (item.get("role") or "") == "assistant":
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(content)
    if summary:
        parts.append(str(summary))
    for report in extra_reports or []:
        if report:
            parts.append(str(report))
    return "\n".join(parts)


def build_restricted_web_query(
    rewritten_question: str,
    *,
    summary: str,
    recent_messages: list[dict[str, Any]],
    extra_reports: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """联网 query 校验器 (两层准入, 见模块 docstring).

    报告范围:
      - RAG Chat 历史里 assistant 角色发的内容 (本会话内)
      - summary (压缩后的历史)
      - extra_reports: 调用方注入的额外语料 (例如来自 AIOps 诊断模块写入 Redis 的最近报告)

    Returns:
        (outgoing_query, topics, skip_reason): skip_reason 非空表示拒绝联网。
    """
    block_reason = _web_query_block_reason(rewritten_question)
    if block_reason:
        return "", [], block_reason

    query = _normalize_web_query(rewritten_question)
    if not query:
        return "", [], "查询为空"

    # Tier 1: 管理员显式配置的常用词 → 原文外发 (显式运维策略)
    topics = _extract_web_topics(rewritten_question)
    if topics:
        return query[:180].strip(), topics, ""

    # Tier 2: white-list by reference —— 候选术语必须出现在参考语料里
    candidate_terms = list(_extract_report_terms(query))[:5]
    if not candidate_terms:
        return "", [], "未能从查询中提取可检索术语"
    reference_terms = _extract_report_terms(
        _reference_corpus(
            summary=summary,
            recent_messages=recent_messages,
            extra_reports=extra_reports,
        )
    )
    matched = [term for term in candidate_terms if term in reference_terms]
    if not matched:
        return (
            "",
            [],
            "查询词未出现在历史诊断报告中, 已跳过联网 (white-list by reference)",
        )
    if len(matched) == len(candidate_terms):
        # 全部候选术语都被引用 → 原文外发, 保留 query 的完整语义
        return query[:180].strip(), matched, ""
    # 部分命中 → 只外发命中的术语, 未引用部分不出境
    outgoing = " ".join(matched)[:180].strip()
    return outgoing, matched, ""


async def build_web_context(
    rewritten_question: str,
    *,
    summary: str,
    recent_messages: list[dict[str, Any]],
    enabled: bool,
) -> tuple[str, list[str], list[dict[str, Any]], str]:
    """联网搜索 + 拼 context.

    Returns:
        (web_context_text, sources, web_hits, skip_reason)
        web_hits: [{"title", "url", "snippet"}, ...]
        skip_reason: 非空表示被跳过的原因 (用于前端展开显示)
    """
    if not enabled:
        return "(本轮未启用联网搜索)", [], [], "本轮未启用联网搜索"
    if not settings.rag_chat_web_search_enabled:
        reason = "联网搜索未启用: RAG_CHAT_WEB_SEARCH_ENABLED=false"
        return f"({reason})", [], [], reason

    try:
        recent_reports = await rag_memory_store.get_recent_diagnosis_reports(limit=3)
    except Exception as e:
        logger.warning(f"[rag-web] 读取最近诊断报告失败: {type(e).__name__}: {e}")
        recent_reports = []
    extra_reports = [r.get("report") or "" for r in recent_reports if r.get("report")]

    query, topics, blocked = build_restricted_web_query(
        rewritten_question,
        summary=summary,
        recent_messages=recent_messages,
        extra_reports=extra_reports,
    )
    if not query:
        logger.info(f"[rag-web] skip | topics={topics} | reason={blocked}")
        return f"(联网搜索已跳过: {blocked})", [], [], blocked

    provider = get_provider()
    max_results = max(1, min(settings.rag_chat_web_search_max_results, 5))
    logger.info(
        f"[rag-web] search | provider={provider} | topics={topics} | query={query!r}"
    )
    try:
        raw_hits = await asyncio.to_thread(search, query, max_results, provider)
    except Exception as e:
        logger.warning(f"[rag-web] 联网搜索失败: {type(e).__name__}: {e}")
        return (
            f"(联网搜索失败: {type(e).__name__}: {e})",
            [],
            [],
            f"{type(e).__name__}: {e}",
        )

    if not raw_hits:
        return (
            f"(联网搜索未找到与 '{query}' 相关的结果)",
            [],
            [],
            "未找到相关结果",
        )

    text = format_results(raw_hits, provider=provider)
    sources = [f"web:{topic}" for topic in topics] or [f"web:{query[:20]}"]
    web_hits = [
        {
            "title": h.get("title", "")[:120],
            "url": h.get("url", "")[:300],
            "snippet": (h.get("snippet", "") or "")[:240],
        }
        for h in raw_hits
    ]
    return text, sources, web_hits, ""
