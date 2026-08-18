"""Harness 通用基础能力。

这里保留跨模型、Provider 和运行环境复用的基础模块；RAG、MCP、Tools 等
语义明确的能力分别位于同级 Harness 子包。本包不做聚合 re-export，避免
隐式 eager-import 与循环依赖。
"""
