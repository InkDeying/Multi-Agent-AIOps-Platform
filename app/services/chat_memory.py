"""RAG Chat Redis 存储的兼容门面.

真实存储实现位于 ``app.db.rag_chat_memory``。
新代码应直接依赖 DB 适配器；保留本门面是为了兼容旧的服务层导入路径。
"""

from app.db.rag_chat_memory import (
    append_diagnosis_report,
    append_message,
    clear_session,
    get_messages,
    get_recent_diagnosis_reports,
    get_recent_messages,
    get_summary,
    is_available,
    load_session,
    replace_messages,
    set_summary,
)

__all__ = [
    "append_diagnosis_report",
    "append_message",
    "clear_session",
    "get_messages",
    "get_recent_diagnosis_reports",
    "get_recent_messages",
    "get_summary",
    "is_available",
    "load_session",
    "replace_messages",
    "set_summary",
]
