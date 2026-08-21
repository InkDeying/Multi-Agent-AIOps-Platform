"""中性 RAG 能力层。

放这里而不是 app/services/rag/: build_context 同时被 services/rag_service (上层编排)
和 tools/knowledge_tool (诊断工具) 使用; 放在 services/ 会让 tools 反向 import services,
违反"tools 是底层原语、services 是编排"的分层。提升到中性位置, 两侧都干净依赖它。

本包同时承载可复用的 query 改写、历史摘要压缩、Embedding、文档切分、向量检索和 Rerank
等 RAG 能力; Redis 会话状态和 SSE 编排仍属于 services 层。
"""

from app.harness.rag.retrieval import build_context

__all__ = ["build_context"]
