"""Team Reports page: service averages and public route."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.services.team_reports import (
    build_team_report_rows,
    format_team_report_display,
    team_report_categories,
)


class TeamReportsServiceTest(unittest.TestCase):
    def test_categories_include_overview_and_offense(self) -> None:
        cats = {c.key: c for c in team_report_categories()}
        self.assertIn("overview", cats)
        self.assertIn("offense", cats)
        self.assertIn("defense", cats)
        self.assertIn("mental", cats)
        self.assertIn("physical", cats)
        overview_keys = {c.key for c in cats["overview"].columns}
        self.assertIn("age", overview_keys)
        self.assertIn("skating", overview_keys)
        self.assertIn("ability", overview_keys)
        offense_keys = {c.key for c in cats["offense"].columns}
        self.assertIn("passing", offense_keys)
        self.assertNotIn("skating", offense_keys)

    def test_format_height_display(self) -> None:
        self.assertEqual(format_team_report_display(74.0, "height"), "6'2\"")
        self.assertEqual(format_team_report_display(None, "height"), "—")

    def test_build_rows_returns_teams(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            rows = build_team_report_rows(db.session)
            self.assertTrue(len(rows) >= 1)
            row = rows[0]
            self.assertIsNotNone(row.team)
            self.assertGreaterEqual(row.player_count, 0)
            self.assertIn("player_count", row.values)


class TeamReportsRoutesTest(unittest.TestCase):
    def test_team_reports_renders_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            app = create_app(make_league_config(slug))
            with app.test_client() as client:
                r = client.get("/team-reports")
                self.assertEqual(r.status_code, 200, slug)
                self.assertIn(b"Team Reports", r.data)
                self.assertIn(b"data-sortable", r.data)
                self.assertIn(b"Overview", r.data)
                self.assertIn(b"Offense", r.data)


if __name__ == "__main__":
    unittest.main()
