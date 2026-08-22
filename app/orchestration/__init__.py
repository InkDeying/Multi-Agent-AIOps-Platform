"""诊断编排层 (Orchestration)。

  - diagnosis_runner: fast/deep 图共享运行器,产 SSE 事件流
  - audit: 给 LangGraph 调用补 AgentRun/ToolCall/Evidence 审计的包装
  - deep_context: deep 图启动前的持久化任务/事件组事实与 Wiki 上下文加载

AgentRun/ToolCall 仓储位于 ``app/db/agent_runs.py``。
与 app/harness/runtime/ 的区别: runtime/ 是 agent 执行的底层原语 (harness/tool_runner/
transitions/stream_sink), orchestration/ 是上层编排, 调用 runtime/。
"""
