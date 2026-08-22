"""Milvus 表达式字面量转义的契约测试.

背景: delete_chunks_by_source 曾把文件名直接内插进
``source == "{source}"`` —— 文件名含双引号/反斜杠会产生非法表达式
(文档无法删除), 也可构造逃逸 + ``or`` 条件的表达式注入 (越权删除)。
所有动态字符串必须经 expr_literal 转成封闭字面量。
"""

from __future__ import annotations

import unittest

from app.db.milvus import expr_literal


class ExprLiteralTests(unittest.TestCase):
    def test_plain_filename_unchanged_inside_quotes(self) -> None:
        self.assertEqual(expr_literal("runbook.md"), '"runbook.md"')

    def test_single_quote_is_legal_and_not_escaped(self) -> None:
        # Milvus 字面量以双引号包裹, 单引号是合法内容字符
        self.assertEqual(expr_literal("it's broken.md"), '"it\'s broken.md"')

    def test_double_quote_escaped(self) -> None:
        self.assertEqual(expr_literal('say".md'), '"say\\".md"')

    def test_backslash_escaped_before_quote(self) -> None:
        self.assertEqual(expr_literal("a\\b.md"), '"a\\\\b.md"')

    def test_backslash_and_quote_combined(self) -> None:
        # 先转义反斜杠再转义双引号, 顺序不能反
        self.assertEqual(expr_literal('a\\"b'), '"a\\\\\\"b"')

    def test_injection_payload_stays_a_single_literal(self) -> None:
        payload = 'a" or source != "x'
        literal = expr_literal(payload)
        # 内部所有双引号都必须被转义, 无法逃出字符串拼接 or 条件
        inner = literal[1:-1]
        self.assertTrue(all(
            ch != '"' or inner[i - 1] == "\\"
            for i, ch in enumerate(inner)
        ))

    def test_empty_and_non_str_inputs(self) -> None:
        self.assertEqual(expr_literal(""), '""')


if __name__ == "__main__":
    unittest.main()
