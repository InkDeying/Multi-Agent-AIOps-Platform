"""API 写操作 Token 门禁的单一实现.

约定 (所有 Token 门禁共用, 曾在 documents.py 有一份同构副本, 已收敛到本模块):
- Token 未配置 → 403 锁定, detail 指明对应配置项, 不做静默放行;
- Token 不匹配 → 403;
- 比较全部走 ``secrets.compare_digest`` (按 UTF-8 字节), 避免时序侧信道。

保护范围 (见 app/main.py 路由注册):
- ADMIN_TOKEN      / X-Admin-Token      : 审批决定 / 诊断任务删除 / Skill 重载;
- KB_ADMIN_TOKEN   / X-KB-Admin-Token   : 知识库上传与删除;
- WEBHOOK_API_KEYS / X-API-Key|Bearer   : Alertmanager webhook 入账。
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import settings


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _digest_matches(candidate: str, expected: str) -> bool:
    return secrets.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    )


def _require_token(
    provided: str,
    expected: str,
    *,
    config_name: str,
    scope: str,
    header_alias: str,
) -> None:
    """共享门禁检查: 未配置即锁定, 不匹配拒绝, 常量时间比较。"""
    expected = str(expected or "").strip()
    if not expected:
        raise _forbidden(f"{scope}已锁定, 请先配置 {config_name}")
    if not provided or not _digest_matches(provided, expected):
        raise _forbidden(f"无权限执行{scope} ({header_alias} 无效)")


def require_admin_token(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> None:
    """控制面写操作的门禁: 校验 X-Admin-Token 与 ADMIN_TOKEN."""
    _require_token(
        x_admin_token,
        settings.admin_token,
        config_name="ADMIN_TOKEN",
        scope="控制面写操作",
        header_alias="X-Admin-Token",
    )


def require_kb_admin_token(
    x_kb_admin_token: str = Header(default="", alias="X-KB-Admin-Token"),
) -> None:
    """知识库写操作的门禁: 校验 X-KB-Admin-Token 与 KB_ADMIN_TOKEN."""
    _require_token(
        x_kb_admin_token,
        settings.kb_admin_token,
        config_name="KB_ADMIN_TOKEN",
        scope="知识库写操作",
        header_alias="X-KB-Admin-Token",
    )


def parse_webhook_api_keys(raw: str) -> list[str]:
    """把逗号分隔的密钥配置拆成去空白后的列表."""
    return [key.strip() for key in raw.split(",") if key.strip()]


def require_webhook_api_key(
    x_api_key: str = Header(default="", alias="X-API-Key"),
    authorization: str = Header(default=""),
) -> None:
    """webhook 门禁: X-API-Key 或 Bearer 密钥须命中 WEBHOOK_API_KEYS 之一."""
    keys = parse_webhook_api_keys(settings.webhook_api_keys)
    if not keys:
        raise _forbidden("webhook 已锁定, 请先配置 WEBHOOK_API_KEYS")
    candidate = x_api_key.strip()
    if not candidate and authorization[:7].lower() == "bearer ":
        candidate = authorization[7:].strip()
    if not candidate or not any(_digest_matches(candidate, key) for key in keys):
        raise _forbidden("webhook 密钥无效 (X-API-Key / Authorization: Bearer)")
