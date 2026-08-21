"""AIOps 多智能体诊断接口 (流式 SSE).

POST /api/v1/aiops/diagnose
  -> 接收 DiagnosisRequest (session_id, query, diagnosis_mode)
  -> 返回 SSE 事件流, 事件类型见 schemas/aiops.py EventType
"""

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.api import rate_limit
from app.schemas.aiops import DiagnosisRequest
import app.services.aiops_service as aiops_service
from app.services import diagnosis_submission_service

router = APIRouter(prefix="/aiops", tags=["aiops"])


class DiagnoseSubmitRequest(BaseModel):
    """手动诊断『提交排队』请求 (改造文档第 2 步)。"""

    query: str = Field(..., min_length=1, max_length=4000, description="告警内容 / 故障描述")
    mode: str = Field(default="fast", description="fast / deep")
    session_id: str = Field(default="web-submit", description="来源会话标识")
    severity: str = Field(default="warning", description="critical / warning / info")
    service: str = Field(default="", description="可选: 关联服务/实例")


@router.post("/diagnose/submit", summary="提交手动诊断到队列 (异步, 立即返回 task_id)")
async def submit_diagnose(req: DiagnoseSubmitRequest, request: Request) -> dict[str, Any]:
    """请求接入与重任务执行解耦: API 只创建任务 + 入队, 重活交给后台 Worker。

    和旧的同步 `/aiops/diagnose` (SSE 内联跑) 并存, 互不影响:
      - 高并发场景用本接口, API 立刻返回, 不被长诊断拖住;
      - 任务进 Postgres + Redis Streams, Worker 按 worker_diagnosis 并发槽慢慢消费;
      - 返回 queue_position 让前端显示『前方还有 N 个』。
    """
    # 限流 (改造文档第 8 步): 单 IP 每分钟手动诊断次数上限, 超限 429
    await rate_limit.enforce(
        "manual", rate_limit.client_ip(request),
        settings.rate_limit_manual_per_ip_per_min, 60,
    )
    try:
        return await diagnosis_submission_service.submit(
            query=req.query,
            mode=req.mode,
            session_id=req.session_id,
            severity=req.severity,
            service=req.service,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建任务失败: {exc}")


@router.post(
    "/diagnose",
    summary="AIOps 多智能体故障诊断 (流式)",
    description=(
        "基于 LangGraph Plan-Execute-Replan 模式的多智能体故障诊断.\n\n"
        "**SSE 事件类型**:\n"
        "- `start` - 流程启动\n"
        "- `plan` - Planner 完成, 给出初始诊断步骤\n"
        "- `step_complete` - Executor 完成单步, 含工具调用结果\n"
        "- `replan` - Replanner 调整剩余计划\n"
        "- `report` - 最终诊断报告 (Markdown)\n"
        "- `complete` - 流程结束\n"
        "- `error` - 异常\n\n"
        "**事件格式** (event=message):\n"
        "```json\n"
        '{\n'
        '  "type": "step_complete",\n'
        '  "stage": "step_executed",\n'
        '  "message": "完成第 2 步",\n'
        '  "data": {"iteration": 2, "step": "...", "result_preview": "..."}\n'
        '}\n'
        "```"
    ),
)
async def aiops_diagnose(req: DiagnosisRequest, request: Request) -> EventSourceResponse:
    # 限流 (改造文档第 8 步): 同步诊断和 submit 共用单 IP/分钟 上限
    await rate_limit.enforce(
        "manual", rate_limit.client_ip(request),
        settings.rate_limit_manual_per_ip_per_min, 60,
    )
    logger.info(
        f"[aiops] session={req.session_id}, mode={req.diagnosis_mode.value}, "
        f"q={req.query[:60]}..."
    )

    async def event_generator() -> AsyncIterator[dict]:
        try:
            async for sse_event in aiops_service.stream_diagnose(
                req.query,
                session_id=req.session_id,
                diagnosis_mode=req.diagnosis_mode,
            ):
                yield {
                    "event": "message",
                    "data": json.dumps(sse_event, ensure_ascii=False),
                }
        except Exception as e:
            logger.exception(f"[aiops] stream 异常: {e}")
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "stage": "stream_failure",
                        "message": str(e),
                        "data": {"error_type": type(e).__name__},
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())
