"""Mobile and tablet layout foundations."""
from __future__ import annotations

import unittest
from pathlib import Path


class MobileLayoutTest(unittest.TestCase):
    def test_base_template_viewport_and_touch_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('viewport-fit=cover', text)
        self.assertIn("site-touch-layout", text)

    def test_site_css_has_global_touch_rules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        self.assertIn("body.site-touch-layout", css)
        self.assertIn("hover-preview-card--dock", css)
        self.assertIn("100dvh", css)
        self.assertIn("70dvh", css)
        self.assertIn("82dvh", css)
        self.assertIn(".home-power-split", css)
        self.assertIn("grid-template-columns: 1fr;", css)

    def test_trade_tool_has_touch_chip_tap(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "trade_tool.html").read_text(encoding="utf-8")
        self.assertIn("wireTradeChipTap", text)

    def test_site_js_has_touch_hover_helpers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("function isTouchLikeDevice()", text)
        self.assertIn("dockHoverCard", text)
        self.assertIn("function bindLongPressPreview(", text)
        # First tap must navigate; do not intercept with preventDefault preview.
        self.assertNotIn(
            "e.preventDefault();\n              showFor(a, playerId);",
            text,
        )
        self.assertNotIn(
            "e.preventDefault();\n              showFor(a, slug);",
            text,
        )


if __name__ == "__main__":
    unittest.main()
