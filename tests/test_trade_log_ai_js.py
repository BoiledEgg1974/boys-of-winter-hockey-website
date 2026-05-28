"""Trade log AI browser request guardrails."""
from __future__ import annotations

import unittest
from pathlib import Path


class TradeLogAiJsTests(unittest.TestCase):
    def test_ai_fetch_sends_csrf_header_and_handles_text_errors(self) -> None:
        js = Path("app/static/js/trade_log.js").read_text(encoding="utf-8")
        self.assertIn('"X-CSRFToken": csrf', js)
        self.assertIn("return r.text().then", js)
        self.assertIn("JSON.parse(text)", js)


if __name__ == "__main__":
    unittest.main()
