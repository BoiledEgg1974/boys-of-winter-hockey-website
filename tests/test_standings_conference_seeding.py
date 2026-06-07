"""Standings conference tab uses playoff division-winner seeding for Cap/Fantasy."""
from __future__ import annotations

import unittest
from pathlib import Path


class StandingsConferenceSeedingTest(unittest.TestCase):
    def test_standings_route_applies_playoff_seeding_for_cap_and_fantasy(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "main.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn("order_conference_by_playoff_seeding", text)
        self.assertIn("league_uses_conference_division_winner_seeding", text)
        self.assertIn("conference_playoff_seeding", text)

    def test_standings_template_explains_playoff_seeding_order(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "standings.html"
        text = path.read_text(encoding="utf-8")

        self.assertIn("conference_playoff_seeding", text)
        self.assertIn("division winners rank 1–3", text)

    def test_standings_streak_badges_use_semantic_color_classes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "standings.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")

        self.assertIn("standings-badge--streak-{{ streak_key }}", template)
        for kind in ("win", "undefeated", "winless", "losing"):
            self.assertIn(f".standings-badge--streak-{kind}", css)


if __name__ == "__main__":
    unittest.main()
