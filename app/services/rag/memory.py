"""RAG Chat 的会话记忆持久化编排.

LLM 驱动的 query 改写和历史摘要压缩位于 ``app.harness.rag.memory``。
本模块只负责 Redis 会话读取、压缩阈值判断、消息裁剪和结果写回。
"""

from __future__ import annotations

from loguru import logger

from app.config import settings
from app.harness.rag.memory import summarize_history
import app.db.rag_chat_memory as session_store

# 兼容现有服务层测试和旧调用方；实际实现已经位于 app/db。
chat_memory = session_store


async def compact_if_needed(session_id: str) -> None:
    """超过 max_messages 时, 把较早消息合并进 summary."""
    if not settings.rag_chat_memory_enabled or not settings.rag_chat_compact_enabled:
        return
    all_messages = await session_store.get_messages(session_id)
    if len(all_messages) <= settings.rag_chat_max_messages:
        return
    keep_count = max(2, settings.rag_chat_compact_keep_messages)
    old_messages = all_messages[:-keep_count]
    recent_messages = all_messages[-keep_count:]
    if not old_messages:
        return
    old_summary = await session_store.get_summary(session_id)
    try:
        summary = await summarize_history(
            max_chars=settings.rag_chat_summary_max_chars,
            old_summary=old_summary or "(无)",
            old_messages=old_messages,
        )
        if summary:
            await session_store.set_summary(
                session_id, summary[: settings.rag_chat_summary_max_chars]
            )
            await session_store.replace_messages(session_id, recent_messages)
            logger.info(
                f"[rag] session={session_id} compact 完成: "
                f"{len(all_messages)} -> {len(recent_messages)} messages"
            )
    except Exception as exc:
        logger.warning(f"[rag] compact 写回失败, 保留原历史: {type(exc).__name__}: {exc}")
