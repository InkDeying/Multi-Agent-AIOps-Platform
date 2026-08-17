# 首期结构优化实施计划

## 目标

在保持 HTTP/SSE、启动命令、配置键、数据库/队列协议和诊断行为不变的前提下，完成已批准方案的阶段 0-2：建立行为保护，清理低风险漂移，解除逻辑依赖环，并将诊断主域整理到清晰目录中。

## 用户与相关方

- 主要：项目所有者和维护者。
- 次要：运行演示的用户、需要理解扩展点的贡献者。

## MVP

1. 建立不依赖外部 Provider 的 characterization tests。
2. 统一诊断模式解析，修正旧路径和失真说明。
3. 解除 permissions/tool_filter 与 mcp_loader/delegate_tools 两个逻辑环。
4. 建立 `app/diagnosis/`，迁移 fast/deep graph、state、runner、audit，并保留旧 import re-export。
5. 拆分 deep graph 与 AgentHarness 的内部职责，外部 facade 不变。

## 非目标

- 不改 Postgres schema、Redis Stream 字段、队列重试语义。
- 不改 HTTP 路径、请求响应、SSE event 名称和启动入口。
- 不重构平铺 Settings，不改 `.env` 键。
- 不搬迁 RAG、前端或第三方 `open-webSearch-main/`；这些属于后续阶段。
- 不做全仓格式化。

## 阶段

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| 0. 环境与行为基线 | 进行中 | 确认可用解释器/依赖，增加测试骨架与现状特征测试 |
| 1. 契约和说明收敛 | 待处理 | 统一 mode 解析，修正旧入口、docstring、忽略规则和兼容命名 |
| 2. 解除逻辑依赖环 | 待处理 | 风险查询归位；ToolCatalog/基础工具注入 |
| 3. Diagnosis 主域迁移 | 待处理 | 新建 diagnosis 包，迁移 fast/deep/runner/audit，旧路径 re-export |
| 4. 大文件内部拆分 | 待处理 | deep graph 节点拆分；Harness prompts/budget/fallback 拆分并保留 facade |
| 5. 全量验证与文档同步 | 待处理 | 测试、AST、JS、Compose、链接/注释、AGENTS/README/ARCHITECTURE 同步 |

## 验收标准

- 原入口 `app.main:app` 与 `python -m app.diagnosis_worker` 不变。
- 原 import 路径与新路径同时通过兼容测试。
- mode 解析、图节点/边、事件转换、权限矩阵、工具分批、Worker 状态决策有测试保护。
- 包括函数内 import 在内的两个已知逻辑环消失。
- safe baseline checks 能运行的部分全部通过，不能运行的部分准确说明原因。

## 风险与策略

- 当前目录无 Git 元数据：不依赖历史/diff，逐文件记录本轮修改；不触碰无关文件。
- `.venv` 启动器失效：优先寻找可用本地环境；不未经确认下载依赖。
- 无既有 tests：先写现状特征测试，再移动实现。
- 兼容 re-export 会短期保留重复入口：以测试锁定，后续阶段再移除。

## 错误记录

| 错误 | 尝试 | 处理 |
| --- | --- | --- |
| 当前目录不是 Git 仓库 | 1 | 以现有文件为事实源，维护显式修改清单 |
| `.venv` Python launcher 失效 | 1 | 待检查其他可用解释器或容器依赖 |

