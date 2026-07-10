"""Admin manual trade log performance guards."""
from __future__ import annotations

import unittest
from pathlib import Path


class AdminTradeLogTemplateTest(unittest.TestCase):
    def test_admin_trade_log_uses_precomputed_paginated_rows(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "admin_trade_log.html").read_text(encoding="utf-8")
        assets_partial = (root / "app" / "templates" / "_manual_trade_log_assets.html").read_text(encoding="utf-8")
        route = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")

        self.assertIn("manual_row_views", template)
        self.assertIn("manual-trade-log__pager", template)
        self.assertIn("_manual_trade_log_assets.html", template)
        self.assertIn("admin_trade_log.js", template)
        self.assertIn("manual-trade-log__asset-row", assets_partial)
        self.assertNotIn("linkify_news_body", template)
        self.assertIn("def _manual_trade_admin_row_views", route)
        self.assertIn("_manual_trade_asset_rows", route)
        self.assertIn("_manual_trade_outgoing_from_form", route)
        self.assertIn("per_page = 50", route)
        self.assertIn("manual_total", route)
        self.assertIn(".manual-trade-log__pager", css)


if __name__ == "__main__":
    unittest.main()
