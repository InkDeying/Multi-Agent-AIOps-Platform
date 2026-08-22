"""分层收口的守护测试.

背景:
- api/skills.py 曾越过 Service 直连 Harness 并在路由文件里定义全部响应模型;
- rag_service.py 曾复制一份 Qwen 模型名单并硬编码 enable_thinking (Provider
  参数拼装), 与 harness/core/llm.py 的名单各自漂移。

本文件用源码断言防止这两类回流, 并覆盖 harness 侧 thinking 适配的行为。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.harness.core import llm as llm_module
from app.harness.core.llm import get_chat_llm, supports_thinking

REPO_ROOT = Path(__file__).parent.parent
SKILLS_API_SOURCE = (REPO_ROOT / "app" / "api" / "skills.py").read_text(
    encoding="utf-8"
)
RAG_SERVICE_SOURCE = (REPO_ROOT / "app" / "services" / "rag_service.py").read_text(
    encoding="utf-8"
)


class LayeringGuardTests(unittest.TestCase):
    """分层规则 (ARCHITECTURE §10) 的源码级断言, 手法同 deep 节点的导入守护."""

    def test_skills_api_goes_through_service_not_harness(self) -> None:
        self.assertNotIn(
            "app.harness",
            SKILLS_API_SOURCE,
            "api/skills.py 不得直连 harness, 应走 app/services/skill_service.py",
        )
        self.assertIn("skill_service", SKILLS_API_SOURCE)

    def test_rag_service_has_no_provider_thinking_params(self) -> None:
        self.assertNotIn(
            "enable_thinking",
            RAG_SERVICE_SOURCE,
            "enable_thinking 是 Provider 参数, 只允许出现在 app/harness/core/llm.py",
        )
        self.assertNotIn("_supports_thinking", RAG_SERVICE_SOURCE)


class SupportsThinkingTests(unittest.TestCase):
    def test_model_list_is_the_merged_single_source(self) -> None:
        # 合并了 rag_service 旧名单 (5 项) 与 llm 旧名单 (3 项)
        for model in ("qwen3-max", "qwen-plus-latest", "qwen-max-latest", "qwq-32b", "qvq-72b"):
            self.assertTrue(supports_thinking(model), model)

    def test_non_thinking_models_rejected(self) -> None:
        for model in ("", "deepseek-v4-pro", "llama3", "qwen2.5-7b", None):
            with self.subTest(model=model):
                # None/空串安全返回 False 而不是抛错
                self.assertFalse(supports_thinking(model or ""))


class GetChatLlmThinkingTests(unittest.TestCase):
    """验证 thinking 参数只在 DashScope 分支落到 extra_body (mock 构造, 无网络)."""

    def _chat_openai_kwargs(self, **call_kwargs):
        with mock.patch.object(llm_module, "ChatOpenAI") as ctor, \
                mock.patch.object(
                    llm_module, "_should_use_local_llm", return_value=False
                ):
            get_chat_llm(**call_kwargs)
        return ctor.call_args.kwargs

    def test_thinking_true_enables_for_supported_streaming_model(self) -> None:
        kwargs = self._chat_openai_kwargs(
            model="qwen3-max", streaming=True, thinking=True
        )
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": True})

    def test_thinking_false_non_streaming_disables_for_supported_model(self) -> None:
        kwargs = self._chat_openai_kwargs(model="qwen3-max", streaming=False)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    def test_streaming_default_injects_nothing_for_supported_model(self) -> None:
        kwargs = self._chat_openai_kwargs(model="qwen3-max", streaming=True)
        self.assertNotIn("extra_body", kwargs)

    def test_thinking_ignored_for_unsupported_model(self) -> None:
        kwargs = self._chat_openai_kwargs(
            model="qwen2.5-7b", streaming=True, thinking=True
        )
        self.assertNotIn("extra_body", kwargs)

    def test_thinking_ignored_for_deepseek_provider(self) -> None:
        kwargs = self._chat_openai_kwargs(
            model="deepseek-v4-pro", streaming=True, thinking=True
        )
        # DeepSeek 分支剥离 enable_thinking, 默认关思考 (thinking 参数不作用于它)
        self.assertNotIn("enable_thinking", str(kwargs.get("extra_body")))


if __name__ == "__main__":
    unittest.main()
