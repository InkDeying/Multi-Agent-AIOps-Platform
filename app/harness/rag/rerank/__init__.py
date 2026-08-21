"""Reranker 调度入口 (按 provider 分发到 dashscope / local 后端).

为什么要加 Reranker
======================
向量检索 (bi-encoder) 把 query 和 doc 分别编码成向量后算 cosine, 这是一次"粗排":
  - 优点: 预先算好 doc 向量, 查询时只算 query 向量 + 一次 ANN 搜索, 延迟低
  - 局限: query 和 doc 从未在同一个模型上下文中交互过, 对细粒度语义差异不敏感
         (比如 "Redis 内存占用高" vs "Redis 内存泄漏排查", 向量很接近但问的是不同事)

Reranker (cross-encoder) 把 (query, doc) 作为一对一起送进模型, 能捕捉精细的语义关联:
  - 优点: 准确度显著高于 bi-encoder (Anthropic 实测 top-20 失败率从 3.7% 降到 1.9%)
  - 局限: 每对都要跑一次模型, 无法提前算好 → 只能用在"粗排后重排少量候选"这一步

典型流水线
======================
  用户 query ─▶ (Hybrid: BM25 ∪ Vector) 取 top-20 ─▶ Rerank ─▶ 取 top-3 ─▶ LLM

后端
======================
  - ``dashscope.py``: gte-rerank-v2 HTTP API, 延迟低但消耗额度
  - ``local.py``:     FlagEmbedding FlagReranker, 默认适配 BAAI/bge-reranker-v2-m3

降级策略
======================
任何异常都返回原始 docs 的前 top_n 项, 不阻断业务:
  - API Key 缺失       → 直接降级
  - 网络超时           → 降级
  - 响应格式异常       → 降级
  - docs 为空          → 直接返回空
"""

from __future__ import annotations

from typing import List, Optional

from langchain_core.documents import Document
from loguru import logger

from app.config import settings
from app.harness.rag.rerank.dashscope import rerank_docs_dashscope
from app.harness.rag.rerank.local import rerank_docs_local


async def rerank_docs(
    query: str,
    docs: List[Document],
    *,
    top_n: Optional[int] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> List[Document]:
    """对候选文档做 Rerank, 返回按相关性降序排列的 top_n 个.

    Args:
        query:   用户原始问题
        docs:    粗排候选 (通常 10-30 个)
        top_n:   返回多少个 (None = settings.rag_top_k)
        model:   rerank 模型名 (None = settings.rag_rerank_model)
        timeout: 单次调用超时秒 (None = settings.rag_rerank_timeout_sec)

    Returns:
        List[Document]: 重排后的 top_n 文档 (原 Document 对象, 附加
        doc.metadata["rerank_score"] 表示 reranker 给出的分数; 发生降级时
        无该字段).

    保证:
        永不抛异常. 任何故障都降级为 docs[:top_n].
    """
    top_n = top_n if top_n is not None else settings.rag_top_k
    model = model or settings.rag_rerank_model
    timeout = timeout if timeout is not None else settings.rag_rerank_timeout_sec

    if not docs:
        return []
    if top_n <= 0:
        return []

    provider = (settings.rag_rerank_provider or "dashscope").lower().strip()
    if provider == "local":
        return await rerank_docs_local(query, docs, top_n=top_n, model=model)
    if provider != "dashscope":
        logger.warning(f"[rerank] 未知 provider={provider!r}, 降级到粗排前 top_n")
        return docs[:top_n]

    return await rerank_docs_dashscope(
        query, docs, top_n=top_n, model=model, timeout=timeout
    )
