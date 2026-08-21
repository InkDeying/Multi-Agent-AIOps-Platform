"""RAG Chat 的记忆能力: query 改写与历史摘要压缩.

本模块只负责 LLM 驱动的文本变换, 不读取或写入 Redis, 也不感知 session_id。
会话阈值、消息裁剪和持久化由 ``app.services.rag.memory`` 负责。

prompt 模板来自 ``harness/prompts/rag.py``, 模型档位直接读 settings ——
RAG 能力包不经过 runtime 门面, 避免能力子包反向依赖 runtime。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from loguru import logger

from app.config import settings
from app.harness.core.llm import get_chat_llm
from app.harness.core.llm_parse import content_to_text
from app.harness.prompts import rag as rag_prompts


def format_history(messages: list[dict[str, Any]]) -> str:
    """把会话消息渲染成用于 RAG Prompt 的多行文本."""
    if not messages:
        return "(无)"
    lines = []
    for item in messages:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:1200]}")
    return "\n".join(lines) if lines else "(无)"


def build_rewrite_prompt(*, summary: str, history: str, question: str) -> str:
    """填充查询改写模板."""
    return rag_prompts.RAG_REWRITE_TEMPLATE.format(
        summary=summary,
        history=history,
        question=question,
    )


def build_compact_prompt(
    *,
    max_chars: int,
    old_summary: str,
    old_messages: str,
) -> str:
    """填充历史压缩模板."""
    return rag_prompts.RAG_COMPACT_TEMPLATE.format(
        max_chars=max_chars,
        old_summary=old_summary,
        old_messages=old_messages,
    )


async def rewrite_question(
    question: str,
    *,
    summary: str,
    recent_messages: list[dict[str, Any]],
) -> str:
    """用历史上下文把当前问题改写为独立检索 query, 失败时回退原文."""
    if not summary and not recent_messages:
        return question
    try:
        prompt = build_rewrite_prompt(
            summary=summary or "(无)",
            history=format_history(recent_messages),
            question=question,
        )
        llm = get_chat_llm(
            model=settings.dashscope_router_model,
            temperature=0,
            streaming=False,
            timeout=20,
            max_retries=1,
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = content_to_text(resp.content).strip().strip("\"'")
        if rewritten:
            logger.info(f"[rag] query rewrite: {question[:80]} -> {rewritten[:120]}")
            return rewritten[:1000]
    except Exception as exc:
        logger.warning(
            f"[rag] query rewrite 失败, 使用原始问题: {type(exc).__name__}: {exc}"
        )
    return question


async def summarize_history(
    *,
    max_chars: int,
    old_summary: str,
    old_messages: list[dict[str, Any]],
) -> str | None:
    """把较早的 RAG Chat 历史压缩成新摘要.

    Returns:
        新摘要文本; 没有可压缩消息、LLM 返回空文本或调用失败时返回 ``None``。
    """
    if not old_messages:
        return None
    try:
        prompt = build_compact_prompt(
            max_chars=max_chars,
            old_summary=old_summary or "(无)",
            old_messages=format_history(old_messages),
        )
        llm = get_chat_llm(
            model=settings.dashscope_chat_model,
            temperature=0,
            streaming=False,
            timeout=40,
            max_retries=1,
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        summary = content_to_text(resp.content).strip()
        if summary:
            return summary[: max(1, int(max_chars))]
    except Exception as exc:
        logger.warning(f"[rag] history summarization 失败: {type(exc).__name__}: {exc}")
    return None
