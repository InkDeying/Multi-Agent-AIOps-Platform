"""RAG Chat 会话用例服务.

API 层只负责 HTTP/SSE 适配；Redis 会话事实由 ``app.db.rag_chat_memory`` 保存。
"""

from __future__ import annotations

from typing import Any

from app.db import rag_chat_memory


async def get_history(session_id: str) -> dict[str, Any]:
    """读取会话摘要与消息历史."""
    session = await rag_chat_memory.load_session(session_id)
    return {
        "session_id": session_id,
        "memory_enabled": await rag_chat_memory.is_available(),
        "summary": session.get("summary") or "",
        "messages": session.get("messages") or [],
    }


async def clear_session(session_id: str) -> dict[str, Any]:
    """清空会话记忆."""
    return {
        "session_id": session_id,
        "cleared": await rag_chat_memory.clear_session(session_id),
    }
