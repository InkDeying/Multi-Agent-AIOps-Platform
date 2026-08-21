from __future__ import annotations

import importlib
import unittest


class ImportCompatibilityTests(unittest.TestCase):
    def test_current_entry_modules_import(self) -> None:
        for name in (
            "app.agents",
            "app.agents.fast",
            "app.agents.fast.nodes.executor",
            "app.agents.deep",
            "app.agents.deep.nodes.evidence_reducer",
            "app.agents.delegates",
            "app.orchestration.diagnosis_runner",
            "app.harness.runtime.tool_filter",
            "app.agents.tool_catalog",
            "app.harness.tools.loader",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(importlib.import_module(name))


if __name__ == "__main__":
    unittest.main()
