"""Homepage League Leaders split (RS / PS / PO)."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config


class HomepageLeadersTests(unittest.TestCase):
    def test_leaders_endpoint_po_differs_from_rs_on_historical(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            rs = client.get("/api/homepage/leaders?segment=rs").get_json()
            po = client.get("/api/homepage/leaders?segment=po").get_json()
        self.assertEqual(rs["segment"], "rs")
        self.assertEqual(po["segment"], "po")
        rs_goals = rs["leaders"]["goals"]
        po_goals = po["leaders"]["goals"]
        self.assertTrue(rs_goals, "expected RS goal leaders")
        if po_goals:
            self.assertNotEqual(
                [r["player_id"] for r in rs_goals[:3]],
                [r["player_id"] for r in po_goals[:3]],
                "PO leaders should not mirror RS top three when playoff stats exist",
            )

    def test_home_template_uses_leaders_endpoint_for_split(self) -> None:
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("/api/homepage/leaders?segment=", html)
        self.assertIn("function loadLeaders()", html)
        self.assertNotRegex(
            html,
            r"seg-pick[\s\S]{0,400}loadSummary\(\)",
            "segment picks should not reload the full dashboard summary",
        )


if __name__ == "__main__":
    unittest.main()
