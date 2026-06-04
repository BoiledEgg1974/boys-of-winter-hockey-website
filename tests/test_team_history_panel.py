"""Team page season history table display."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.player_career_totals import goalie_career_lines_totals


class TeamHistoryPanelTemplateTest(unittest.TestCase):
    def test_season_history_includes_win_pct_and_totals(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "_team_history_panel.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Winning percentage", text)
        self.assertIn("team-record-history__totals-row", text)
        self.assertIn("TOTAL", text)
        self.assertIn("totals_pct", text)
        self.assertIn("(r.t_otl or 0) * 0.5", text)

    def test_player_goalie_history_includes_win_pct(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "player.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Winning percentage", text)
        self.assertIn("t.win_pct", text)
        self.assertIn("ln.ties_otl or 0", text)

    def test_goalie_totals_include_win_pct(self) -> None:
        totals = goalie_career_lines_totals(
            [
                SimpleNamespace(
                    gp=10,
                    wins=6,
                    losses=3,
                    ties_otl=1,
                    shutouts=2,
                    goals_against=20,
                    shots_against=300,
                ),
                SimpleNamespace(
                    gp=5,
                    wins=2,
                    losses=2,
                    ties_otl=1,
                    shutouts=0,
                    goals_against=15,
                    shots_against=150,
                ),
            ]
        )
        self.assertEqual(totals["gp"], 15)
        self.assertAlmostEqual(totals["win_pct"], (8 + 1.0) / 15)


if __name__ == "__main__":
    unittest.main()
