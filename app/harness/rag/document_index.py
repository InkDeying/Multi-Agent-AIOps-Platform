"""RAG 文档索引能力。

本模块负责文档分块、向量写入、文档来源聚合、按 source 删除和 BM25 刷新。
FastAPI 类型、API 响应模型和 HTTP 语义不进入这里；Milvus 原生访问统一通过
``app.db.milvus`` 完成。
"""

from __future__ import annotations

from typing import Dict, List

from loguru import logger

from app.config import settings
from app.db import milvus as milvus_store
from app.exceptions import VectorStoreError
from app.harness.rag.splitter import split_markdown
from app.harness.rag.vector_store import get_vector_store


def index_document(content: str, *, source: str) -> int:
    """切分并写入一份文档，返回写入的 chunk 数量。"""
    chunks = split_markdown(content, source=source)
    if not chunks:
        return 0
    try:
        get_vector_store().add_documents(chunks)
    except Exception as exc:
        logger.exception(f"[document-index] 写入向量库失败: {exc}")
        raise VectorStoreError(f"向量库写入失败: {exc}") from exc
    logger.info(f"[document-index] {source}: 索引 {len(chunks)} 个 chunk")
    refresh_bm25_best_effort()
    return len(chunks)


def list_indexed_documents() -> List[Dict[str, object]]:
    """列出所有已索引文档，按 source 聚合。"""
    if not milvus_store.milvus_manager.has_collection():
        return []
    counts = milvus_store.count_chunks_by_source()
    return [
        {"source": source, "chunk_count": count}
        for source, count in sorted(counts.items())
    ]


def delete_indexed_document(source: str) -> int:
    """按 source 删除所有相关 chunks。"""
    if not milvus_store.milvus_manager.has_collection():
        return 0
    try:
        deleted = milvus_store.delete_chunks_by_source(source)
    except Exception as exc:
        logger.exception(f"[document-index] 删除失败: {exc}")
        raise VectorStoreError(f"删除失败: {exc}") from exc
    if deleted:
        logger.info(f"[document-index] 删除 {source}: {deleted} 个 chunk")
        refresh_bm25_best_effort()
    return deleted


def refresh_bm25_best_effort() -> None:
    """重建 BM25 索引，失败只告警，不阻断文档主操作。"""
    if not (settings.rag_hybrid_enabled and settings.rag_bm25_refresh_on_upload):
        return
    try:
        from app.harness.rag.hybrid_retriever import refresh_bm25_index

        refresh_bm25_index()
    except Exception as exc:
        logger.warning(
            f"[document-index] BM25 刷新失败 (忽略): {type(exc).__name__}: {exc}"
        )
