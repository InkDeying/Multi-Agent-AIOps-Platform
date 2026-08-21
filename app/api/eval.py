"""评估结果只读 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import eval_service

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/reports", summary="列出最近评估报告")
async def list_reports(
    limit: int = Query(20, ge=1, le=200),
    mode: str | None = Query(None, description="可选: 只列某种模式 (retrieval / ragas)"),
) -> dict[str, Any]:
    return eval_service.list_reports(limit, mode)


@router.get("/reports/{name}", summary="读取某份评估报告")
async def get_report(
    name: str,
    include_details: bool = Query(False),
) -> dict[str, Any]:
    try:
        return eval_service.get_report(name, include_details)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/reports/{name}/low-scores", summary="挑出低分题 (用于补语料)")
async def list_low_scores(
    name: str,
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    metric: str = Query(
        "faithfulness",
        description="ragas 指标: faithfulness / answer_relevancy / context_precision / context_recall",
    ),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return eval_service.list_low_scores(name, threshold, metric, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
