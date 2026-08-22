"""Skill 查询接口的请求/响应模型.

与 app/schemas/document.py 同一模式: API 层只做 HTTP 与异常映射,
模型定义集中在 schemas, 业务逻辑在 app/services/skill_service.py。
"""

from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field


class SkillSummary(BaseModel):
    """Skill 给前端看的精简元信息."""

    name: str = Field(..., description="Skill 唯一标识")
    display_name: str = Field(..., description="人类可读名称")
    description: str = Field(..., description="一句话适用场景")
    category: str = Field(default="", description="分类")
    platforms: List[str] = Field(default_factory=list, description="兼容平台")
    tags: List[str] = Field(default_factory=list, description="标签")
    triggers: List[str] = Field(default_factory=list, description="触发关键字")
    allowed_tools: List[str] = Field(default_factory=list, description="允许调用的工具白名单")
    risk_level: str = Field(..., description="风险等级: low / medium / high")
    context: str = Field(default="inline", description="执行模式: inline / fork")
    source_path: str | None = Field(default=None, description="源 SKILL.md 路径")
    linked_files: List[str] = Field(default_factory=list, description="支持文件相对路径")


class SkillListData(BaseModel):
    """Skill 列表响应载荷."""

    total: int = Field(..., description="Skill 总数")
    skills: List[SkillSummary] = Field(default_factory=list, description="全部 Skill 元信息")


class SkillDetailData(SkillSummary):
    """单个 Skill 详情."""

    playbook: str = Field(default="", description="SKILL.md Markdown body")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展 metadata")


class SkillFileData(BaseModel):
    """Skill 支持文件内容."""

    name: str = Field(..., description="Skill name")
    path: str = Field(..., description="Skill 目录内的相对路径")
    content: str = Field(default="", description="文件内容")


def summary_from_skill(skill: Any) -> SkillSummary:
    """把 registry 的 Skill 域对象转成响应模型."""
    return SkillSummary(**skill.to_summary())
