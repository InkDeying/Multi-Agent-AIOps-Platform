"""文档管理用例服务。

本层只负责文件类型、编码校验和用例编排，不依赖 FastAPI、Milvus SDK 或 API 响应模型。
RAG 索引细节位于 ``app.harness.rag.document_index``。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from loguru import logger

from app.exceptions import UnsupportedFileTypeError
from app.harness.rag.document_index import (
    delete_indexed_document,
    index_document,
    list_indexed_documents,
)

ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt"}


async def upload_document(filename: str, raw: bytes) -> Tuple[int, int]:
    """校验文件内容并交给 RAG 索引能力写入向量库。

    Returns:
        ``(chunks_indexed, bytes)``，响应模型由 API 层构造。
    """
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"不支持的文件类型 '{ext}', 仅支持 {sorted(ALLOWED_EXTENSIONS)}"
        )
    if not raw:
        raise UnsupportedFileTypeError("文件为空")

    bytes_count = len(raw)
    logger.info(f"[document] 收到上传: {filename} ({bytes_count} bytes)")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileTypeError(f"文件不是 UTF-8 编码: {exc}") from exc

    chunks_indexed = index_document(content, source=filename)
    if not chunks_indexed:
        raise UnsupportedFileTypeError(f"文件 {filename} 切分后无有效内容")
    return chunks_indexed, bytes_count


def list_documents() -> List[Dict[str, object]]:
    """返回按 source 聚合后的已索引文档信息。"""
    return list_indexed_documents()


def delete_document(source: str) -> int:
    """删除指定 source 的文档。"""
    return delete_indexed_document(source)


def _get_extension(filename: str) -> str:
    """提取扩展名 (含点, 小写)."""
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()
