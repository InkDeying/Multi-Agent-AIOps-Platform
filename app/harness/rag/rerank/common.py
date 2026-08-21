"""两个 rerank 后端共用的文本构造."""

from __future__ import annotations

from langchain_core.documents import Document

from app.config import settings


def rerank_text(doc: Document) -> str:
    """构造给 cross-encoder 的文本。

    检索命中的是 child chunk, 但真正给 LLM 的是 parent_content。reranker 如果只看
    child, 会错过排障步骤和上下文; 同时保留 child 命中片段, 方便模型抓住 query token。
    """
    meta = doc.metadata or {}
    source = str(meta.get("source") or "")
    chapter = str(meta.get("chapter") or "")
    child = doc.page_content.strip()

    if not settings.rag_rerank_use_parent_context:
        return child

    parent = str(meta.get("parent_content") or "").strip()
    max_chars = max(200, int(settings.rag_rerank_parent_max_chars or 1200))
    if parent and len(parent) > max_chars:
        parent = parent[:max_chars] + "...(truncated)"

    parts = []
    if source:
        parts.append(f"Source: {source}")
    if chapter:
        parts.append(f"Chapter: {chapter}")
    if parent:
        parts.append(f"Parent context:\n{parent}")
    parts.append(f"Matched child:\n{child}")
    return "\n\n".join(parts)
