"""联网搜索 white-list by reference 准入的契约测试.

背景: build_restricted_web_query 曾在 docstring 声称 "只允许搜索历史诊断报告
里出现过的实体", 但 summary / recent_messages / extra_reports 三个参数从未
参与校验 —— 任意未命中敏感规则的查询都会被原文送往外部搜索引擎。
修复后为两层准入: 管理员关键词放行 + 参考语料术语白名单。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.services.rag import web_context
from app.services.rag.web_context import build_restricted_web_query

# 显式指定关键词表, 测试不依赖仓库默认配置
_KEYWORDS = SimpleNamespace(rag_chat_web_search_keywords="redis,k8s")


def _call(question, *, summary="", messages=None, reports=None):
    with mock.patch.object(web_context, "settings", _KEYWORDS):
        return build_restricted_web_query(
            question,
            summary=summary,
            recent_messages=messages or [],
            extra_reports=reports,
        )


class SensitiveRuleFirstTests(unittest.TestCase):
    def test_ip_query_rejected_even_with_admin_keyword(self) -> None:
        query, topics, reason = _call("redis 192.168.1.10 连不上怎么办")
        self.assertEqual(query, "")
        self.assertIn("敏感", reason)

    def test_blocked_keyword_rejected(self) -> None:
        query, _, reason = _call("查一下 api_key 怎么轮换")
        self.assertEqual(query, "")
        self.assertIn("禁止", reason)


class AdminKeywordTierTests(unittest.TestCase):
    def test_admin_keyword_sends_full_query(self) -> None:
        query, topics, reason = _call("帮我搜下 redis 内存溢出怎么处理")
        self.assertEqual(reason, "")
        self.assertIn("redis", topics)
        self.assertIn("redis", query)


class ReferenceWhitelistTierTests(unittest.TestCase):
    def test_all_terms_referenced_sends_full_query(self) -> None:
        query, topics, reason = _call(
            "vmmem 是什么", summary="此前诊断: vmmem 进程内存泄漏"
        )
        self.assertEqual(reason, "")
        self.assertIn("vmmem", topics)
        self.assertIn("是什么", query)  # 全命中 → 原文外发

    def test_partial_match_only_matched_terms_leave(self) -> None:
        query, topics, reason = _call(
            "vmmem 和 prod-db-07 什么关系", summary="vmmem 内存泄漏"
        )
        self.assertEqual(reason, "")
        self.assertIn("vmmem", query)
        self.assertNotIn("prod-db-07", query)  # 未引用实体不出境

    def test_unreferenced_internal_entity_rejected(self) -> None:
        query, topics, reason = _call("prod-db-07 的密码策略")
        self.assertEqual(query, "")
        self.assertEqual(topics, [])
        self.assertIn("未出现在历史诊断报告", reason)

    def test_empty_corpus_rejects_non_keyword_query(self) -> None:
        _, _, reason = _call("vmmem 是什么", summary="", messages=[], reports=[])
        self.assertIn("未出现在历史诊断报告", reason)


class ReferenceScopeTests(unittest.TestCase):
    def test_assistant_messages_count_as_reference(self) -> None:
        query, _, reason = _call(
            "vmmem 怎么限制内存",
            messages=[{"role": "assistant", "content": "vmmem 内存泄漏已处理"}],
        )
        self.assertEqual(reason, "")
        self.assertIn("vmmem", query)

    def test_user_messages_do_not_count_as_reference(self) -> None:
        # 用户自己提过的词不构成白名单, 否则任何内部实体都能自我授权
        _, _, reason = _call(
            "vmmem 怎么限制内存",
            messages=[{"role": "user", "content": "vmmem vmmem"}],
        )
        self.assertIn("未出现在历史诊断报告", reason)

    def test_summary_alone_is_a_valid_reference(self) -> None:
        query, _, reason = _call("vmmem 是什么", summary="vmmem 占用过高")
        self.assertEqual(reason, "")
        self.assertIn("vmmem", query)

    def test_extra_reports_alone_is_a_valid_reference(self) -> None:
        query, _, reason = _call(
            "xxjob-scheduler 是什么",
            reports=["巡检报告: xxjob-scheduler 调度延迟"],
        )
        self.assertEqual(reason, "")
        self.assertIn("xxjob-scheduler", query)

    def test_without_extra_reports_same_query_rejected(self) -> None:
        _, _, reason = _call("xxjob-scheduler 是什么")
        self.assertIn("未出现在历史诊断报告", reason)


if __name__ == "__main__":
    unittest.main()
