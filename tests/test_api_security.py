"""控制面鉴权依赖的行为契约测试.

覆盖:
- ADMIN_TOKEN 门禁: 未配置锁定 / 错误拒绝 / 正确放行;
- WEBHOOK_API_KEYS 门禁: 未配置锁定 / X-API-Key 与 Bearer / 多密钥 / 错误拒绝;
- CORS 中间件只在 CORS_ALLOW_ORIGINS 配置时注册;
- 四个控制面端点确实挂上了对应鉴权依赖.
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.security import (
    parse_webhook_api_keys,
    require_admin_token,
    require_webhook_api_key,
)


class AdminTokenGateTests(unittest.TestCase):
    def test_locked_when_admin_token_unconfigured(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = ""
            with self.assertRaises(HTTPException) as ctx:
                require_admin_token(x_admin_token="whatever")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("ADMIN_TOKEN", str(ctx.exception.detail))

    def test_rejects_wrong_admin_token(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = "s3cret"
            with self.assertRaises(HTTPException) as ctx:
                require_admin_token(x_admin_token="wrong")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_rejects_missing_admin_token(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = "s3cret"
            with self.assertRaises(HTTPException) as ctx:
                require_admin_token(x_admin_token="")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_accepts_matching_admin_token(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = "s3cret"
            self.assertIsNone(
                require_admin_token(x_admin_token="s3cret")
            )


class WebhookApiKeyGateTests(unittest.TestCase):
    def test_locked_when_keys_unconfigured(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = ""
            with self.assertRaises(HTTPException) as ctx:
                require_webhook_api_key(x_api_key="k", authorization="")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("WEBHOOK_API_KEYS", str(ctx.exception.detail))

    def test_rejects_missing_credential(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = "key-a"
            with self.assertRaises(HTTPException) as ctx:
                require_webhook_api_key(x_api_key="", authorization="")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_accepts_x_api_key_from_allowlist(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = "key-a, key-b"
            self.assertIsNone(
                require_webhook_api_key(x_api_key="key-b", authorization="")
            )

    def test_accepts_bearer_credential_case_insensitive(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = "key-a"
            self.assertIsNone(
                require_webhook_api_key(
                    x_api_key="", authorization="bearer key-a"
                )
            )

    def test_rejects_unknown_key(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = "key-a"
            for kwargs in (
                {"x_api_key": "nope", "authorization": ""},
                {"x_api_key": "", "authorization": "Bearer nope"},
            ):
                with self.subTest(**kwargs):
                    with self.assertRaises(HTTPException) as ctx:
                        require_webhook_api_key(**kwargs)
                    self.assertEqual(ctx.exception.status_code, 403)

    def test_parses_and_trims_key_list(self) -> None:
        self.assertEqual(
            parse_webhook_api_keys(" a , ,b ,"),
            ["a", "b"],
        )


class CorsRegistrationTests(unittest.TestCase):
    def _cors_registered(self, configured: str) -> bool:
        from fastapi import FastAPI

        from app.middleware import setup_middlewares

        app = FastAPI()
        with mock.patch("app.middleware.settings") as settings:
            settings.cors_allow_origins = configured
            setup_middlewares(app)
        return any(m.cls is CORSMiddleware for m in app.user_middleware)

    def test_cors_not_registered_by_default(self) -> None:
        self.assertFalse(self._cors_registered(""))

    def test_cors_registered_for_wildcard_and_explicit_origins(self) -> None:
        self.assertTrue(self._cors_registered("*"))
        self.assertTrue(self._cors_registered("http://localhost:5173"))


class RouterProtectionContractTests(unittest.TestCase):
    """控制面端点必须保持挂载鉴权依赖, 防止后续改动悄悄摘掉门禁."""

    @staticmethod
    def _route_dependencies(router, path: str, method: str) -> set:
        deps = set()
        for route in router.routes:
            if route.path == path and method in getattr(route, "methods", set()):
                deps.update(
                    d.dependency for d in getattr(route, "dependencies", [])
                )
        return deps

    def test_admin_token_protected_endpoints(self) -> None:
        from app.api import approvals, incidents, skills

        cases = [
            (approvals.router, "/approvals/{req_id}/decide", "POST"),
            (incidents.router, "/incidents/tasks/bulk-delete", "POST"),
            (incidents.router, "/incidents/tasks/{task_id}", "DELETE"),
            (skills.router, "/skills/reload", "POST"),
        ]
        for router, path, method in cases:
            with self.subTest(path=path, method=method):
                deps = self._route_dependencies(router, path, method)
                self.assertIn(require_admin_token, deps)

    def test_webhook_endpoint_requires_api_key(self) -> None:
        from app.api import webhook

        deps = self._route_dependencies(
            webhook.router, "/webhook/alertmanager", "POST"
        )
        self.assertIn(require_webhook_api_key, deps)

    def test_read_endpoints_stay_open(self) -> None:
        from app.api import approvals, skills

        for router, path, method in [
            (approvals.router, "/approvals/pending", "GET"),
            (skills.router, "/skills", "GET"),
        ]:
            with self.subTest(path=path):
                deps = self._route_dependencies(router, path, method)
                self.assertNotIn(require_admin_token, deps)
                self.assertNotIn(require_webhook_api_key, deps)


class HttpGateIntegrationTests(unittest.TestCase):
    """走真实 HTTP 栈验证: 门禁在业务层 (Postgres/Redis) 之前拒绝请求.

    只测 403 路径, 放行路径由上面的依赖级单测覆盖, 避免依赖真实基础设施.
    """

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api import approvals, webhook

        app = FastAPI()
        app.include_router(approvals.router)
        app.include_router(webhook.router)
        return TestClient(app)

    def test_decide_blocked_without_token(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = "s3cret"
            resp = self._client().post(
                "/approvals/req-1/decide",
                json={"decision": "approved"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_decide_blocked_with_wrong_token(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.admin_token = "s3cret"
            resp = self._client().post(
                "/approvals/req-1/decide",
                json={"decision": "approved"},
                headers={"X-Admin-Token": "wrong"},
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_blocked_without_key(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = "key-a"
            resp = self._client().post(
                "/webhook/alertmanager",
                json={"alerts": []},
            )
        self.assertEqual(resp.status_code, 403)

    def test_webhook_locked_when_unconfigured(self) -> None:
        with mock.patch("app.api.security.settings") as settings:
            settings.webhook_api_keys = ""
            resp = self._client().post(
                "/webhook/alertmanager",
                json={"alerts": []},
                headers={"X-API-Key": "anything"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("WEBHOOK_API_KEYS", str(resp.json().get("detail")))


if __name__ == "__main__":
    unittest.main()
