"""Incident 与诊断任务的 API (列表/详情/证据链 + 手动升级入口)。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.security import require_admin_token
from app.services import incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


class FromChatRequest(BaseModel):
    """聊天升级为事件的请求体."""

    session_id: str = Field(default="web-chat", description="聊天会话 id, 用于关联")
    query: str = Field(..., min_length=1, max_length=4000, description="升级原因 / 用户原话")
    title: str = Field(default="", max_length=200, description="可选简短标题, 不填用 query 前 80 字")
    severity: str = Field(default="warning", description="critical / warning / info")
    service: str = Field(default="", description="若聊天里提到具体服务/实例, 带过来便于关联")
    diagnosis_mode: str = Field(default="fast", description="fast / deep")
    chat_excerpt: str = Field(default="", description="可选: 最近几轮聊天文本, 写入 incident metadata 留痕")
    rag_hits: list[dict[str, Any]] = Field(default_factory=list, description="可选: RAG 命中片段, 写入 metadata")


class BulkDeleteTasksRequest(BaseModel):
    """批量删除事件历史。"""

    task_ids: list[str] = Field(..., min_length=1, max_length=100)


@router.get("/tasks", summary="列出最近的诊断任务")
async def list_tasks(limit: int = 20) -> dict[str, Any]:
    return await incident_service.list_tasks(limit=limit)


@router.post(
    "/tasks/bulk-delete",
    summary="批量删除已结束的诊断任务",
    dependencies=[Depends(require_admin_token)],
)
async def bulk_delete_tasks(req: BulkDeleteTasksRequest) -> dict[str, Any]:
    return await incident_service.bulk_delete_tasks(req.task_ids)


@router.get("/tasks/{task_id}", summary="查诊断任务详情")
async def get_task(task_id: str) -> dict[str, Any]:
    """按 task_id 取单条任务的完整事实行 (含 payload/status/attempts)。

    pending 任务额外带 queue_position (排队位置, 1-based), 供前端显示『前方还有 N 个』。
    """
    task = await incident_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.delete(
    "/tasks/{task_id}",
    summary="删除一条已结束的诊断任务",
    dependencies=[Depends(require_admin_token)],
)
async def delete_task(task_id: str) -> dict[str, Any]:
    """删除历史任务及其审计记录；运行中的任务必须先结束。"""
    try:
        result = await incident_service.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="task not found")
    return result


@router.get("/tasks/{task_id}/agent-runs", summary="列出某任务下的 AgentRun")
async def list_task_agent_runs(task_id: str) -> dict[str, Any]:
    """看某次诊断起了几个 AgentRun (含 usage/token/状态)。"""
    try:
        return await incident_service.list_task_agent_runs(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/tool-calls", summary="列出某任务下的 ToolCall")
async def list_task_tool_calls(task_id: str) -> dict[str, Any]:
    """看某次诊断调过的所有工具 (名称/参数/耗时/状态)。"""
    try:
        return await incident_service.list_task_tool_calls(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/evidence", summary="列出某任务下的 Evidence")
async def list_task_evidence(task_id: str, limit: int = 100) -> dict[str, Any]:
    """看某次诊断产生的全部证据链 (alert_payload/tool_call/diagnosis_step/report)。"""
    try:
        return await incident_service.list_task_evidence(task_id, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/from_chat", summary="把一次聊天升级为诊断事件")
async def create_incident_from_chat(req: FromChatRequest) -> dict[str, Any]:
    """打通 Chat ↔ Incident 的孤岛: 用户在 RAG 聊天里发现真问题时一键升级.

    行为:
      1. 调 IncidentRepository.create_manual_task 写入事实表 (alerts / incident_groups / incidents / diagnosis_tasks)
      2. 若 incident_pipeline_enabled, 把任务推入 Redis Stream 让 Worker 接管异步诊断
      3. 返回 task_id / incident_group_id, 前端跳到事件中心 + 选中
    """
    try:
        return await incident_service.create_incident_from_chat(
            session_id=req.session_id,
            query=req.query,
            title=req.title,
            severity=req.severity,
            service=req.service,
            diagnosis_mode=req.diagnosis_mode,
            chat_excerpt=req.chat_excerpt,
            rag_hits=req.rag_hits,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建事件失败: {exc}")


@router.get("/groups/{incident_group_id}", summary="查 IncidentGroup 详情")
async def get_incident_group(incident_group_id: str) -> dict[str, Any]:
    """按 incident_group_id 取告警组概览 (主服务/严重等级/告警条数)。"""
    group = await incident_service.get_incident_group(incident_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="incident group not found")
    return group


@router.get("/groups/{incident_group_id}/evidence", summary="列出某 IncidentGroup 下的 Evidence")
async def list_incident_group_evidence(
    incident_group_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    """跨同组多次诊断聚合证据 (复发故障复盘视图)。"""
    group = await incident_service.get_incident_group(incident_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="incident group not found")
    return await incident_service.list_incident_group_evidence(
        incident_group_id,
        limit=limit,
    )
