# 首期结构优化进度

## 2026-08-17

- 用户批准按建议实施首期 MVP（阶段 0-2）。
- 确认当前目录仍无 Git 元数据，根级只有一个 AGENTS.md。
- 读取 planning-with-files 工作流并完成 session catchup。
- 创建实施计划、发现记录和进度记录。
- 用户完成首次 Git 提交：`91a3a03 first commit`，当前 `main` 工作区干净。
- 用户确认最终目录方案：`agents/fast`、`agents/deep`、`agents/delegates`；fast/deep 使用 `graph + state + nodes`。
- 检查发现提交后的仓库没有 `tests/`，下一步先恢复无外部依赖的行为基线。
- 已恢复 7 个 characterization tests，fast/deep 图、SSE 信封、mode alias、工具分批和当前入口测试全部通过。
- 新增 `app/harness/tools/catalog.py`；`mcp_loader` 只加载基础工具，delegate 执行只读取基础工具，解除双方逻辑环。
- 工具目录验证结果保持为基础工具 6 个、delegate 工具 3 个、合并后 9 个。
- 完成 `agents/delegates` 与 `agents/fast/{graph,state,nodes}` 迁移，迁移后行为测试通过。
- 完成 `agents/deep/{graph,state,nodes}` 迁移；将 933 行 deep graph 拆成 58 行装配器和独立节点模块。
- 新旧 deep 实现经 EvidencePlan、EvidenceReducer、RemediationPlanner 和 Report 确定性样例对比一致。
- 删除 `app/diagnosis_graphs` 和旧的 `app/agents/subagents`，同步 runner、tool consumers、README、ARCHITECTURE 和中英文 AGENTS 指南。
- 最终验证：10 个 unittest 通过；全仓 compileall、Node 语法、Compose 配置和 `git diff --check` 通过；Ruff 未安装。

## 2026-08-17：第二期方案分析

- 用户提出 API 扁平化、中间件上移、services 重组，以及将 Agent 共享能力统一为 Harness 工程。
- 已核对 `services`、`rag`、`skills`、`tools`、`wiki`、`runtime` 的实际调用关系。
- 初步结论：`incidents/evidence` 保持领域事实边界，`orchestration` 保持应用编排边界，不并入普通 services。
- 推荐建立独立顶层 `app/harness/`，收拢 runtime、skills、tools、rag、wiki；不建议放入 `app/agents/harness/`，避免把共享平台误认为一种 Agent。
- 当前仅完成方案设计和持久化记录，未执行第二期代码迁移。
- 用户确认以统一的 AI/AIOps Harness 为顶层引擎：`app/harness/core` 整体迁移为 `app/harness/core/`，RAG/MCP/Tools 语义明确的文件分别归入对应 Harness 子目录。
- 用户批准实施，并要求进一步甄别 Core：数据库辅助归入 `app/db`，Redis 并发与限流归入 `app/queue`；API 只移动文件和必要 imports，services 本轮不重组。
- 第二期阶段 1 已开始，正在建立 Harness 并执行纯结构迁移。
- 已完成 Harness、DB、Queue 的文件归类移动；API 路由文件已原样上移到 `app/api/`，中间件已移动到 `app/middleware.py`。
- 修正 Wiki 因目录加深导致的仓库根定位，运行时数据仍指向原 `data/wiki/`。
- 完成全部旧路径扫描，源码、脚本、测试和架构文档不再依赖已删除的顶层 `core/rag/runtime/skills/tools/wiki` 或 `api/v1` 包路径。
- 验证通过：Python 3.12.13 bundled runtime 下 `compileall`、10 个 unittest、Harness/DB/Queue 入口导入、Wiki/Skill 路径检查、`docker compose config --quiet`、`git diff --check`。
- Docker 配置仅报告本机 `C:\Users\Moran\.docker\config.json` 权限警告，不影响 Compose 配置解析。
