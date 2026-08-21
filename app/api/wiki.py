"""LLM Wiki 只读 API (经验沉淀展示)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services import wiki_service

router = APIRouter(prefix="/wiki", tags=["wiki"])


@router.get("/overview", summary="Wiki 总览 (是否启用 + 页面计数)")
async def wiki_overview() -> dict[str, Any]:
    return wiki_service.overview()


@router.get("/pages", summary="列出所有 wiki 页面")
async def list_pages(
    category: str | None = Query(None, description="可选 services / patterns"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    return wiki_service.list_pages(category, limit)


@router.get("/pages/{category}/{slug}", summary="读取单个 wiki 页 (markdown 原文)")
async def get_page(category: str, slug: str) -> dict[str, Any]:
    try:
        return wiki_service.get_page(category, slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取失败: {exc}") from exc


@router.get("/index", summary="Wiki 索引页 (index.md)")
async def get_index() -> dict[str, Any]:
    return wiki_service.get_index()


@router.get("/log", summary="最近的 ingest 流水 (log.md)")
async def get_log(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return wiki_service.get_log(limit)
