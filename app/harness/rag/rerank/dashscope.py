"""DashScope rerank 后端 (gte-rerank-v2 HTTP API).

接口文档: https://help.aliyun.com/zh/model-studio/developer-reference/text-rerank-api
"""

from __future__ import annotations

from typing import List

import httpx
from langchain_core.documents import Document
from loguru import logger

from app.config import settings
from app.harness.rag.rerank.common import rerank_text

# DashScope Rerank HTTP 接口 (rerank 专用路径, 与 dashscope_base_url 的 LLM 端点是两回事)
_RERANK_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)


async def rerank_docs_dashscope(
    query: str,
    docs: List[Document],
    *,
    top_n: int,
    model: str,
    timeout: float,
) -> List[Document]:
    """DashScope rerank 实现."""

    # 1) 前置校验: 没有 API key 直接降级, 避免无意义的 401
    api_key = settings.dashscope_api_key
    if not api_key or api_key.startswith("sk-your"):
        logger.warning("[rerank] 无 API key, 降级到粗排前 top_n")
        return docs[:top_n]

    # 2) 构造请求
    # DashScope 要求 documents 是 str 列表; 我们保留下标映射, 重排后用下标取回 Document
    doc_texts = [rerank_text(d) for d in docs]

    payload = {
        "model": model,
        "input": {
            "query": query,
            "documents": doc_texts,
        },
        "parameters": {
            "top_n": min(top_n, len(docs)),
            "return_documents": False,  # 不需要回传原文, 省带宽
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 3) 调用
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_RERANK_ENDPOINT, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning(f"[rerank] 超时 ({timeout}s), 降级到粗排前 top_n")
        return docs[:top_n]
    except httpx.HTTPStatusError as e:
        logger.warning(f"[rerank] HTTP {e.response.status_code}: {e.response.text[:200]}")
        return docs[:top_n]
    except Exception as e:
        logger.warning(f"[rerank] 调用失败, 降级: {type(e).__name__}: {e}")
        return docs[:top_n]

    # 4) 解析响应
    # DashScope 返回形如 {"output": {"results": [{"index": 2, "relevance_score": 0.87}, ...]}}
    try:
        results = data.get("output", {}).get("results") or []
        if not results:
            logger.warning(f"[rerank] 响应 results 为空, 降级. raw={str(data)[:200]}")
            return docs[:top_n]

        reranked: List[Document] = []
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score")
            if idx is None or not (0 <= idx < len(docs)):
                continue
            doc = docs[idx]
            # 写分数到 metadata (不修改原对象, 复制一个)
            new_meta = dict(doc.metadata or {})
            if score is not None:
                new_meta["rerank_score"] = float(score)
            reranked.append(
                Document(page_content=doc.page_content, metadata=new_meta)
            )
            if len(reranked) >= top_n:
                break

        if not reranked:
            return docs[:top_n]

        logger.info(
            f"[rerank] ok: query={query[:40]!r} "
            f"candidates={len(docs)} -> top_n={len(reranked)} "
            f"top1_score={reranked[0].metadata.get('rerank_score'):.3f}"
        )
        return reranked

    except Exception as e:
        logger.warning(f"[rerank] 解析响应失败, 降级: {type(e).__name__}: {e}")
        return docs[:top_n]
