# 首期结构优化发现

## 已知基线

- 118 个 Python 文件可由 AST 解析。
- `frontend/app.js` 通过 Node 语法检查。
- `docker compose config --quiet` 通过，本机 Docker config 有权限警告。
- 已知逻辑环：`permissions <-> tool_filter`、`mcp_loader <-> delegate_tools`。
- 首要大文件：deep graph、AgentHarness、IncidentRepository；本期只拆前两者。
- 用户最终确认 Agent 目录边界为 `fast`、`deep`、`delegates`，fast/deep 内部均采用 `graph + state + nodes`。
- 现有 `subagents` 是通过 `delegate_to_*` 暴露的通用委托能力，应迁移为根级 `agents/delegates`，不归属 fast 或 deep。
- `app/harness/tools/loader.py` 当前只装配基础工具，`app/harness/tools/catalog.py` 再合并 delegate tools，已解除与 delegate runner 的逻辑环。
- deep graph 共 933 行，可按 context、evidence plan、四个 specialist、reducer、RCA、remediation、report 拆为节点模块。

## 待记录

- 提交后的仓库没有 `tests/`，需要先恢复 characterization tests。
- 每次迁移后的 import 和契约差异。
- 工具目录解环后，本地无 MCP 环境下 `get_base_tools()` 返回 6 个工具，`catalog.get_all_tools()` 返回原有 9 个工具。
- `app/agents/deep/graph.py` 从 933 行收敛为 58 行；节点分别位于 `deep/nodes/`，确定性输出与旧实现一致。
- 最终代码引用中已不存在 `app.diagnosis_graphs`、旧平铺 Agent 模块或 `agents/subagents`。

## 第二期结构分析

- `app/services/aiops_service.py` 是 API/SSE 面向的诊断用例入口，实际图执行在 `app/orchestration/diagnosis_runner.py`，建议改为 `services/diagnosis_service.py`。
- `app/services/rag_service.py` 是聊天用例编排，不是底层 RAG 检索库；建议改为 `services/chat_service.py`。
- `app/services/chat_memory.py`、`app/services/rag/memory.py`、`message_utils.py`、`web_context.py` 共同组成聊天用例内部辅助模块，建议收拢到 `services/chat/`。
- `app/services/document_service.py` 是知识文档管理用例，暂时保留在 services，后续如知识管理扩展再单独建立 `services/knowledge/`。
- `app/harness/rag/retrieval.py` 是中性检索原语，被 `services/rag_service.py`、`tools/knowledge_tool.py`、benchmark 和脚本共同使用，不应并入某个单一 service；建议迁移到 `app/harness/rag/`。
- `app/evidence`、`app/incidents` 是 Postgres 事实领域；`app/orchestration` 是诊断执行编排和审计，三者均不属于普通 services。
- `app/harness/skills` 同时被 API 和 Agent 使用；`app/harness/tools` 是 Agent 工具目录/MCP 边界；`app/harness/runtime` 是执行、权限、审批和状态流；`app/harness/wiki` 是运行时经验存储；四者适合作为 `app/harness/` 的共享子系统。
- `app/api/v1` 当前只通过 `app.main` 注册，路由 URL 前缀由 `API_PREFIX = "/api/v1"` 提供，因此可以扁平移动而不改变外部接口。
- `app/middleware.py` 只被 `app.main` 使用，由 `app/api/middleware.py` 移动而来，不改变中间件行为。

### Core 目录拆分判断（修订）

- Harness 被定义为整个 AI/AIOps 引擎，而非只含 Agent 执行循环；因此原 `app/core/` 的内容可以整体纳入 Harness，再按职责分流。
- `embedding.py`、`hybrid_retriever.py`、`reranker.py`、`splitter.py`、`vector_store.py`、`milvus.py` 与原 `app/rag/retrieval.py` 一起放入 `app/harness/rag/`。
- `mcp_client.py` 和 `lazy_mcp_tools.py` 放入 `app/harness/mcp/`；工具定义、目录、元数据和装配保留在 `app/harness/tools/`。
- `db_utils.py` 是 Repository 共享持久化辅助，迁移为 `app/db/utils.py`。
- `distributed_limiter.py` 和 `rate_limiter.py` 都复用 `app.queue.redis_streams.incident_queue` 的 Redis 连接，迁移到 `app/queue/`。
- 其余通用 Core 模块放入 `app/harness/core/`：`llm.py`、`llm_health.py`、`llm_parse.py`、`structured.py`、`web_search.py`。
- 结论：`app/core` 不再作为顶层目录；内容分别归入 Harness、DB 和 Queue。

## 第二期迁移结果

- `app/core/` 已拆分：通用能力进入 `app/harness/core/`，RAG 能力进入 `app/harness/rag/`，MCP 客户端进入 `app/harness/mcp/`。
- `app/core/db_utils.py` 已移动为 `app/db/utils.py`；`distributed_limiter.py`、`rate_limiter.py` 已移动到 `app/queue/`。
- `app/rag/`、`app/runtime/`、`app/skills/`、`app/tools/`、`app/wiki/` 已整体收拢到 `app/harness/` 对应子目录。
- `app/api/v1/*.py` 已原样上移到 `app/api/`；`app/api/middleware.py` 已移动为 `app/middleware.py`。
- Wiki 根目录从目录深度变化中得到保护，仍解析到仓库根下的 `data/wiki/`；Skill definitions 仍解析到 `app/harness/skills/definitions/`。
- 服务层本轮未重组，仅更新因路径迁移产生的 imports。
