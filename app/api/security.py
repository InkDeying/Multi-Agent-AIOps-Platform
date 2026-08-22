"""控制面写操作与 webhook 的鉴权依赖.

沿用 ``documents.require_kb_admin_token`` 的既定约定:
- 密钥未配置 → 403 锁定, detail 指明对应配置项, 不做静默放行;
- 密钥不匹配 → 403;
- 比较全部走 ``secrets.compare_digest`` (按 UTF-8 字节), 避免时序侧信道.

保护范围 (见 app/main.py 路由注册):
- ADMIN_TOKEN  / X-Admin-Token      : 审批决定 / 诊断任务删除 / Skill 重载;
- WEBHOOK_API_KEYS / X-API-Key|Bearer: Alertmanager webhook 入账.
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


def parse_webhook_api_keys(raw: str) -> list[str]:
    """把逗号分隔的密钥配置拆成去空白后的列表."""
    return [key.strip() for key in raw.split(",") if key.strip()]


def require_admin_token(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> None:
    """控制面写操作的门禁: 校验 X-Admin-Token 与 ADMIN_TOKEN."""
    expected = settings.admin_token.strip()
    if not expected:
        raise _forbidden("控制面写操作已锁定, 请先配置 ADMIN_TOKEN")
    if not x_admin_token or not _digest_matches(x_admin_token, expected):
        raise _forbidden("无权限执行控制面写操作 (X-Admin-Token 无效)")


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
