"""Wiki 文件行格式协议的契约测试.

背景: log.md / index.md 的行格式曾在写端 (harness/wiki/store.py) 和读端
(services/wiki_service.py) 各自硬编码, 格式变更会静默漂移 (读端跳过不认识
的行, 不报错)。现在格式单一定义在 harness/wiki/text_utils.py, 本测试用
render->parse 往返 + 跨模块读写往返守住这一定义。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.harness.wiki import store as wiki_store
from app.harness.wiki.text_utils import (
    format_index_line,
    format_log_line,
    is_index_line,
    parse_log_line,
)
from app.services import wiki_service


class LogLineRoundTripTests(unittest.TestCase):
    def test_render_then_parse_recovers_date_and_entry(self) -> None:
        line = format_log_line("diagnosis | checkout redis 超时", date="2026-08-22")
        parsed = parse_log_line(line)
        self.assertEqual(
            parsed, {"date": "2026-08-22", "entry": "diagnosis | checkout redis 超时"}
        )

    def test_multiline_entry_collapses_to_single_line(self) -> None:
        line = format_log_line("a\nb   c\t d", date="2026-01-01")
        self.assertNotIn("\n", line)
        self.assertEqual(parse_log_line(line)["entry"], "a b c d")

    def test_parse_rejects_non_log_lines(self) -> None:
        for bad in (
            "普通文本",
            "### [2026-08-22] 级别不对",
            "## [2026/08/22] 日期格式不对",
            "## [] 空日期",
            "- [[patterns/redis]] 目录行不是流水行",
            "",
        ):
            with self.subTest(line=bad):
                self.assertIsNone(parse_log_line(bad))


class IndexLineTests(unittest.TestCase):
    def test_index_line_format_and_recognition(self) -> None:
        line = format_index_line("patterns/redis-oom", "Redis OOM 排查")
        self.assertEqual(line, "- [[patterns/redis-oom]] — Redis OOM 排查")
        self.assertTrue(is_index_line(line))
        self.assertFalse(is_index_line("# Wiki 目录"))
        self.assertFalse(is_index_line(""))

    def test_summary_truncated_and_blank_falls_back_to_ref(self) -> None:
        long_summary = "x" * 200
        self.assertTrue(len(format_index_line("patterns/x", long_summary)) < 200)
        self.assertEqual(
            format_index_line("patterns/x", "   "),
            "- [[patterns/x]] — patterns/x",
        )


class CrossModuleDriftGuardTests(unittest.TestCase):
    """写端 (store) 与读端 (wiki_service) 必须共享同一格式定义 —— 漂移即失败."""

    def test_log_written_by_store_is_readable_by_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "log.md"
            with mock.patch.object(wiki_store, "WIKI_LOG_FILE", log_path), \
                    mock.patch.object(wiki_service, "WIKI_DIR", Path(tmp)):
                wiki_store._append_log("diagnosis | fast | redis 超时")
                wiki_store._append_log("diagnosis | deep | 磁盘打满")
                result = wiki_service.get_log(limit=10)

        self.assertEqual(result["count"], 2)
        # get_log 倒序返回: 最新在前
        self.assertIn("磁盘打满", result["items"][0]["entry"])
        self.assertIn("redis 超时", result["items"][1]["entry"])
        for item in result["items"]:
            self.assertRegex(item["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_index_written_by_store_keeps_only_index_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "index.md"
            with mock.patch.object(wiki_store, "WIKI_INDEX_FILE", index_path):
                wiki_store._update_index("patterns/redis", "Redis 故障")
                wiki_store._update_index("patterns/disk", "磁盘故障")
                # 同 ref 更新走去重, 不会产生两行同 ref
                wiki_store._update_index("patterns/redis", "Redis 故障 v2")
                content = index_path.read_text(encoding="utf-8")

        refs = [ln for ln in content.splitlines() if is_index_line(ln)]
        self.assertEqual(len(refs), 2)
        self.assertIn("Redis 故障 v2", content)
        self.assertNotIn("Redis 故障\n", content.replace("Redis 故障 v2", ""))


if __name__ == "__main__":
    unittest.main()
