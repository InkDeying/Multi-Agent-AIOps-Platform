"""Skill 查询与重载的用例服务.

API 层只做 HTTP、鉴权与异常映射; 对 SkillRegistry 的读与重载收口在这里,
保持 API -> Services -> Harness 的单向依赖 (见 docs/ARCHITECTURE.md 分层规则)。
"""

from __future__ import annotations

from typing import Any

from app.harness.skills import get_skill_registry, reload_skill_registry


def list_skills() -> list[Any]:
    """列出全部已注册 Skill (registry 域对象, 由 API 层转响应模型)."""
    return list(get_skill_registry().all())


def reload_skills() -> list[Any]:
    """清空进程内注册表缓存并重扫内置与外部 SKILL.md, 返回重载后的全部 Skill."""
    return list(reload_skill_registry().all())


def get_skill(name: str) -> Any | None:
    """按 name 取单个 Skill; 不存在返回 None, 由 API 层映射 404."""
    return get_skill_registry().get(name.lower())


def read_skill_file(name: str, path: str) -> str:
    """读取 Skill 目录内相对路径文件; FileNotFoundError/ValueError 原样上抛."""
    return get_skill_registry().read_supporting_file(name.lower(), path)
