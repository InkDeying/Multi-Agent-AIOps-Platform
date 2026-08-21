"""RAG Chat 服务层的消息转换工具."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


def history_to_messages(messages: list[dict[str, Any]]) -> list[HumanMessage | AIMessage]:
    """把字典历史转成 LangChain Message 列表."""
    converted: list[HumanMessage | AIMessage] = []
    for item in messages:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if item.get("role") == "user":
            converted.append(HumanMessage(content=content[:2000]))
        elif item.get("role") == "assistant":
            converted.append(AIMessage(content=content[:3000]))
    return converted
