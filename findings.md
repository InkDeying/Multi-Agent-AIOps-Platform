# 首期结构优化发现

## 已知基线

- 118 个 Python 文件可由 AST 解析。
- `frontend/app.js` 通过 Node 语法检查。
- `docker compose config --quiet` 通过，本机 Docker config 有权限警告。
- 已知逻辑环：`permissions <-> tool_filter`、`mcp_loader <-> delegate_tools`。
- 首要大文件：deep graph、AgentHarness、IncidentRepository；本期只拆前两者。

## 待记录

- 可用测试运行环境和依赖情况。
- characterization tests 覆盖的现有行为。
- 每次迁移后的 import 和契约差异。

