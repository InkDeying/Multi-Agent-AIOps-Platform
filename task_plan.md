# 首期结构优化实施计划

## 目标

在保持 HTTP/SSE、启动命令、配置键、数据库/队列协议和诊断行为不变的前提下，将 Agent 主域整理为 `app/agents/fast`、`app/agents/deep` 和 `app/agents/delegates`，删除 `app/diagnosis_graphs`，并解除工具装配与 delegate tools 的逻辑依赖环。

## 用户与相关方

- 主要：项目所有者和维护者。
- 次要：运行演示的用户、需要理解扩展点的贡献者。

## MVP

1. 建立不依赖外部 Provider 的 characterization tests。
2. 将基础工具加载与统一工具目录分离，解除 `mcp_loader <-> delegate_tools` 逻辑环。
3. 建立 `app/agents/delegates`，迁移 delegate 定义与工具包装。
4. 建立 `app/agents/fast`，按 `graph + state + nodes` 组织 fast 图。
5. 建立 `app/agents/deep`，按 `graph + state + nodes` 组织并拆分 deep 图。
6. 删除 `app/diagnosis_graphs`，同步所有代码引用和架构文档。

## 非目标

- 不改 Postgres schema、Redis Stream 字段、队列重试语义。
- 不改 HTTP 路径、请求响应、SSE event 名称和启动入口。
- 不重构平铺 Settings，不改 `.env` 键。
- 不搬迁 orchestration、runtime、RAG、前端或第三方 `open-webSearch-main/`。
- 不做全仓格式化。
- 本轮不拆 AgentHarness，不修改数据库、Redis、HTTP 或 SSE 契约。

## 阶段

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| 0. 环境与行为基线 | 完成 | 补齐 fast/deep 图、事件和 import 特征测试 |
| 1. 工具目录解环 | 完成 | mcp_loader 只加载基础工具，catalog 统一装配 delegates |
| 2. Delegates 与 Fast 迁移 | 完成 | 新建 delegates；fast 按 graph/state/nodes 迁移 |
| 3. Deep 迁移与拆分 | 完成 | deep 按 graph/state/nodes 迁移并删除 diagnosis_graphs |
| 4. 引用与文档同步 | 完成 | 更新 runner、工具消费者、测试、README/ARCHITECTURE/AGENTS |
| 5. 全量验证 | 完成 | unittest、compileall、Compose、旧路径和循环依赖检查 |

## 验收标准

- 原入口 `app.main:app` 与 `python -m app.diagnosis_worker` 不变。
- 新入口 `app.agents.fast`、`app.agents.deep` 和 `app.agents.delegates` 可导入；根 `app.agents` 保留 builder facade。
- mode 解析、图节点/边、事件转换、权限矩阵、工具分批、Worker 状态决策有测试保护。
- `app.diagnosis_graphs` 被删除，代码和文档不存在旧路径引用。
- `mcp_loader <-> delegate tools` 逻辑环消失。
- safe baseline checks 能运行的部分全部通过，不能运行的部分准确说明原因。

## 风险与策略

- 当前仓库已由用户提交，迁移从干净的 `main` 分支提交 `91a3a03` 开始。
- `.venv` 启动器失效：优先寻找可用本地环境；不未经确认下载依赖。
- 无既有 tests：先写现状特征测试，再移动实现。
- 兼容 re-export 会短期保留重复入口：以测试锁定，后续阶段再移除。

## 错误记录

| 错误 | 尝试 | 处理 |
| --- | --- | --- |
| 先前目录没有 Git 元数据 | 1 | 用户已完成首次提交，当前可使用 Git diff 审核迁移 |
| `.venv` Python launcher 失效 | 1 | 使用 Codex Python 3.12，并通过 `PYTHONPATH=.venv/Lib/site-packages` 加载现有依赖 |
| 提交后的仓库没有 `tests/` | 1 | 迁移前重新建立无外部依赖的行为保护测试 |
| 新旧 deep 节点直接比较出现一次 AssertionError | 1 | 差异来自 transition 动态时间戳；归一化 `ts` 后路由、归并、处置和报告完全一致 |
| 当前环境没有 Ruff | 1 | 未下载依赖；其余安全基线均执行并记录 |
| API 路由批量移动遇到 `__init__.py` 同名冲突 | 1 | 路由文件已成功移动；保留 `app/api/__init__.py`，删除旧 `api/v1/__init__.py` |

---

# 第二期应用层与 Harness 结构优化方案（已确认实施）

## 目标

在不改变 HTTP 路径、数据库/队列协议、Agent 行为和运行入口的前提下，
整理 API、服务用例和 Agent 基础设施边界，降低 `services`、`rag`、
`skills`、`tools`、`runtime`、`wiki` 之间的横向依赖。

## 本期明确保留不动

- `app/db/`
- `app/queue/`
- `app/schemas/`
- `app/harness/core/` 不保留：通用内容进入 Harness，数据库辅助进入 `app/db/`，Redis 协调进入 `app/queue/`
- `app/agents/fast/`、`app/agents/deep/`、`app/agents/delegates/` 的图逻辑

## 目标结构

```text
app/
├── api/                         # 路由文件直接位于此处，URL 仍保留 /api/v1
│   ├── aiops.py
│   ├── chat.py
│   ├── documents.py
│   ├── incidents.py
│   ├── skills.py
│   ├── webhook.py
│   ├── queue.py
│   ├── health.py
│   ├── eval.py
│   ├── wiki.py
│   └── approvals.py
├── middleware.py                # HTTP 中间件
├── agents/
│   ├── fast/
│   ├── deep/
│   └── delegates/
├── harness/                     # Agent 共享执行与知识基础设施
│   ├── core/                    # 原 app/harness/core 中的通用基础能力
│   ├── rag/                     # retrieval、embedding、Milvus、rerank、splitter
│   ├── mcp/                     # MCP client 与 lazy MCP tool 支持
│   ├── skills/                  # 原 app/harness/skills
│   ├── tools/                   # 工具定义、目录、元数据和基础工具装配
│   ├── wiki/                    # 原 app/harness/wiki
│   └── runtime/                 # 原 app/harness/runtime
├── services/                    # 面向用户请求的用例服务
│   ├── diagnosis_service.py     # 原 aiops_service.py
│   ├── chat_service.py          # 原 rag_service.py
│   ├── chat/                    # 会话记忆、消息格式化、联网上下文
│   ├── document_service.py
│   └── knowledge/               # 后续需要时承载知识文档用例
├── incidents/                   # 告警、故障组、诊断任务事实
├── evidence/                    # 诊断证据契约与持久化
├── orchestration/               # fast/deep 执行编排与审计
├── db/                          # 原目录保留，并接收 core/db_utils.py
├── queue/                       # 原目录保留，并接收 Redis 限流/并发模块
├── schemas/                     # 保持不动
└── config.py 等应用入口与配置
```

## 目录边界判断

- `incidents`：领域事实与生命周期，不属于普通 `services`。
- `evidence`：诊断证据领域模型与仓储，不属于普通 `services`。
- `orchestration`：诊断用例编排与审计，属于应用编排层，不并入 `services`。
- `services`：只保留 API 面向的业务用例入口和组合逻辑。
- `harness`：整个 AI/AIOps 引擎，包含通用 Core、RAG、MCP、工具、Skill、Wiki 与运行时。

## 阶段

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| 1. Harness 建立与内容分类 | 完成 | 迁移 core/runtime/skills/tools/rag/wiki，并把 DB/Redis/MCP/RAG 内容归位 |
| 2. API 扁平化 | 完成 | `api/v1/*` 原样移到 `api/*`，中间件移到 `app/middleware.py`，保持 `/api/v1` 前缀 |
| 3. 引用与文档同步 | 完成 | 全量更新 imports、启动入口、README、ARCHITECTURE、AGENTS |
| 4. 分阶段验证 | 完成 | compileall、行为测试、路由导入、旧路径扫描、git diff --check |

## 非目标

- 不改变 API URL、SSE 事件、请求响应 schema。
- 不改变 Postgres 表、Redis Stream、Worker 状态和重试语义。
- 不把 `incidents/evidence` 直接塞入 services。
- 不把 deep 专业节点和 delegates 合并。
- 不在本期重写 RAG 算法、权限模型或 Harness 逻辑。

## 主要风险

- `app/harness/rag/retrieval.py` 同时被服务、工具、benchmark、脚本使用，迁移时必须全局更新。
- `app/harness/runtime` 与 `app/harness/tools` 存在双向概念依赖，需保持延迟导入和工具目录解环结果。
- API 路由文件移动不会改变 URL，但 `app.main` 和测试导入必须同步修改。
- `app/harness/skills`、`app/harness/wiki` 既被 API 使用，也被 Agent 使用，不能按纯 Agent 私有代码处理。
- `app/harness/core` 中的 `db_utils` 进入 `app/db/utils.py`，Redis 限流与并发槽进入 `app/queue/`；其余内容按 Core/RAG/MCP 进入 Harness。
