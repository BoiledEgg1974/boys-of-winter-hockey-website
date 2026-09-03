"""Placeholder motion GIFs and the templates that load them."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION = ROOT / "app" / "static" / "img" / "motion"
MAX_BYTES = 400 * 1024
EXPECTED_GIFS = (
    "splash-historical.gif",
    "splash-fantasy.gif",
    "splash-cap.gif",
    "splash-formula.gif",
    "splash-demolition.gif",
    "moment-celebrate.gif",
    "trade-bot-talk.gif",
    "racing-formula.gif",
    "racing-demolition.gif",
)


class PlaceholderMotionTest(unittest.TestCase):
    def test_gif_files_exist_and_stay_small(self) -> None:
        for name in EXPECTED_GIFS:
            path = MOTION / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 500, name)
            self.assertLess(path.stat().st_size, MAX_BYTES, name)
            self.assertEqual(path.read_bytes()[:3], b"GIF")

    def test_hub_splash_has_no_motion_overlays(self) -> None:
        html = (ROOT / "hub" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("league-card__motion", html)
        self.assertNotIn("img/motion/", html)
        for name in EXPECTED_GIFS:
            if name.startswith("splash-"):
                self.assertNotIn(name, html)

    def test_success_moment_and_trade_bot_hooks(self) -> None:
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("img/motion/moment-celebrate.gif", base)
        self.assertIn('id="bowl-moment"', base)
        js = (ROOT / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("initBowlSuccessMoment", js)
        self.assertIn("prefers-reduced-motion", js)
        ap = (ROOT / "app" / "templates" / "action_points.html").read_text(encoding="utf-8")
        self.assertIn('data-bowl-moment="success"', ap)
        ach = (ROOT / "app" / "templates" / "gm_achievements.html").read_text(encoding="utf-8")
        self.assertIn("data-unlocked-at", ach)
        bot = (ROOT / "app" / "templates" / "ai_trade_tool.html").read_text(encoding="utf-8")
        self.assertIn("img/motion/trade-bot-talk.gif", bot)
        tool_js = (ROOT / "app" / "static" / "js" / "ai_trade_tool.js").read_text(encoding="utf-8")
        self.assertIn("is-talking", tool_js)

    def test_racing_pages_load_motion(self) -> None:
        home = (ROOT / "app" / "templates" / "racing" / "home.html").read_text(encoding="utf-8")
        index = (ROOT / "app" / "templates" / "racing" / "results_index.html").read_text(encoding="utf-8")
        detail = (ROOT / "app" / "templates" / "racing" / "results_detail.html").read_text(encoding="utf-8")
        for html in (home, index, detail):
            self.assertIn("img/motion/racing-formula.gif", html)
            self.assertIn("img/motion/racing-demolition.gif", html)


if __name__ == "__main__":
    unittest.main()
