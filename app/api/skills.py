"""Skill 列表查询接口.

GET /api/v1/skills
  -> 列出全部已注册 Skill 的元信息, 供前端展示 Playbook 库
GET /api/v1/skills/{name}
  -> 查看单个 Skill 全文和支持文件索引
GET /api/v1/skills/{name}/files
  -> 读取 Skill 支持文件
POST /api/v1/skills/reload
  -> 重载 SkillRegistry (需 ADMIN_TOKEN)

列表接口不返回 playbook 全文, 避免响应体过大.
模型在 app/schemas/skill.py, registry 访问在 app/services/skill_service.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.common import ApiResponse
from app.schemas.skill import (
    SkillDetailData,
    SkillFileData,
    SkillListData,
    summary_from_skill,
)
from app.api.security import require_admin_token
from app.services import skill_service

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get(
    "",
    response_model=ApiResponse[SkillListData],
    summary="列出全部已注册 Skill",
    description=(
        "返回当前 SkillRegistry 中已加载的全部 Skill 元信息 (不含 playbook 全文).\n\n"
        "Skill 会从内置 `app/harness/skills/definitions/**/SKILL.md` 和可选 `SKILLS_EXTERNAL_DIRS` 加载."
    ),
)
async def list_skills() -> ApiResponse[SkillListData]:
    skills = skill_service.list_skills()
    summaries = [summary_from_skill(s) for s in skills]
    return ApiResponse.success(
        data=SkillListData(total=len(summaries), skills=summaries),
        message=f"已加载 {len(summaries)} 个 Skill",
    )


@router.post(
    "/reload",
    response_model=ApiResponse[SkillListData],
    summary="重新加载 SkillRegistry",
    description="清空进程内 SkillRegistry 缓存并重新扫描内置与外部 SKILL.md.",
    dependencies=[Depends(require_admin_token)],
)
async def reload_skills() -> ApiResponse[SkillListData]:
    skills = skill_service.reload_skills()
    summaries = [summary_from_skill(s) for s in skills]
    return ApiResponse.success(
        data=SkillListData(total=len(summaries), skills=summaries),
        message=f"已重新加载 {len(summaries)} 个 Skill",
    )


@router.get(
    "/{name}",
    response_model=ApiResponse[SkillDetailData],
    summary="查看单个 Skill",
    description="返回单个 Skill 的元信息、playbook 全文和支持文件索引.",
)
async def get_skill(name: str) -> ApiResponse[SkillDetailData]:
    skill = skill_service.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return ApiResponse.success(
        data=SkillDetailData(
            **skill.to_summary(),
            playbook=skill.playbook,
            metadata=skill.metadata,
        ),
        message=f"已加载 Skill: {skill.name}",
    )


@router.get(
    "/{name}/files",
    response_model=ApiResponse[SkillFileData],
    summary="读取 Skill 支持文件",
    description="读取某个 Skill 目录内的相对路径文件; 默认读取 SKILL.md.",
)
async def get_skill_file(
    name: str,
    path: str = Query(default="SKILL.md", description="Skill 目录内相对路径"),
) -> ApiResponse[SkillFileData]:
    try:
        content = skill_service.read_skill_file(name, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.success(
        data=SkillFileData(name=name.lower(), path=path, content=content),
        message="ok",
    )
