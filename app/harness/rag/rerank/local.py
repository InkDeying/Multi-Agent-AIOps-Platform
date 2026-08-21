"""本地 FlagEmbedding rerank 后端 (BAAI/bge-reranker-v2-m3).

模型懒加载 + lru_cache: 应用启动时不碰 torch, 首次 rerank 才加载。
加载失败会记住原因, 本进程内不再重复尝试 (避免每次请求都白等一次大模型下载)。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import List, Optional

from langchain_core.documents import Document
from loguru import logger

from app.config import settings
from app.harness.rag.rerank.common import rerank_text

_local_reranker_load_error: Optional[str] = None


def _resolve_local_device() -> str:
    """选择本地 CrossEncoder 设备."""
    configured = (settings.rag_local_rerank_device or "auto").lower().strip()
    if configured != "auto":
        return configured

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@lru_cache(maxsize=1)
def _get_local_flag_reranker(model_name: str):
    """懒加载本地 reranker, 避免应用启动就加载大模型."""
    global _local_reranker_load_error
    if _local_reranker_load_error:
        raise RuntimeError(
            f"本地 reranker 本进程加载曾失败: {_local_reranker_load_error}"
        )

    try:
        from FlagEmbedding import FlagReranker
    except ImportError as e:
        raise RuntimeError(
            "本地 reranker 需要 FlagEmbedding: pip install FlagEmbedding"
        ) from e

    backend = (settings.rag_local_rerank_backend or "flagembedding").lower().strip()
    if backend != "flagembedding":
        raise RuntimeError(f"不支持的本地 rerank backend: {backend}")

    device = _resolve_local_device()
    max_length = max(128, int(settings.rag_local_rerank_max_length or 512))
    batch_size = max(1, int(settings.rag_local_rerank_batch_size or 8))
    logger.info(
        f"[rerank:local] loading FlagReranker model={model_name}, "
        f"device={device}, max_length={max_length}, batch_size={batch_size}"
    )
    try:
        return FlagReranker(
            model_name,
            devices=device,
            use_fp16=False,
            batch_size=batch_size,
            max_length=max_length,
            trust_remote_code=True,
        )
    except Exception as e:
        _local_reranker_load_error = f"{type(e).__name__}: {e}"
        raise


def _rerank_local_sync(
    query: str,
    docs: List[Document],
    *,
    top_n: int,
    model: str,
) -> List[Document]:
    """同步本地 rerank; 外层用 asyncio.to_thread 调用."""
    try:
        reranker = _get_local_flag_reranker(model)
        pairs = [(query, rerank_text(d)) for d in docs]
        scores = reranker.compute_score(pairs)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if isinstance(scores, tuple):
            scores = list(scores)
        elif not isinstance(scores, list):
            scores = [scores]
    except Exception as e:
        logger.warning(f"[rerank:local] 调用失败, 降级: {type(e).__name__}: {e}")
        return docs[:top_n]

    ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)
    reranked: List[Document] = []
    for idx, score in ranked[:top_n]:
        doc = docs[idx]
        new_meta = dict(doc.metadata or {})
        new_meta["rerank_score"] = float(score)
        reranked.append(Document(page_content=doc.page_content, metadata=new_meta))

    if reranked:
        logger.info(
            f"[rerank:local] ok: query={query[:40]!r} "
            f"candidates={len(docs)} -> top_n={len(reranked)} "
            f"top1_score={reranked[0].metadata.get('rerank_score'):.3f}"
        )
    return reranked or docs[:top_n]


async def rerank_docs_local(
    query: str,
    docs: List[Document],
    *,
    top_n: int,
    model: str,
) -> List[Document]:
    """本地 FlagEmbedding rerank, 放入线程避免阻塞 async loop."""
    return await asyncio.to_thread(
        _rerank_local_sync,
        query,
        docs,
        top_n=top_n,
        model=model,
    )
