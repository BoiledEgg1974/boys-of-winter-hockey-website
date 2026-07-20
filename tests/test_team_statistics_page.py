"""Tests for Team Statistics page."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TeamStatisticsPageTest(unittest.TestCase):
    def test_team_statistics_template_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "team_statistics.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        standings = (root / "app" / "templates" / "standings.html").read_text(encoding="utf-8")
        for marker in (
            "team-statistics-page",
            "team-statistics-filters",
            "team-statistics-filters__toolbar",
            "team-statistics-chart",
            "team-statistics-chart-data",
            "team-statistics-filters__split",
            "team-statistics-cards",
            "team_statistics_page",
        ):
            self.assertIn(marker, template if marker != "team_statistics_page" else main)
        for removed in (
            "Game Logs",
            "Full Season",
            "Score Adjusted",
            "Date Range",
            "Color Coding",
            "team-statistics-filters__row--unavailable",
            "is-disabled",
        ):
            self.assertNotIn(removed, template)
        self.assertIn("Team Statistics", (root / "app" / "templates" / "base.html").read_text(encoding="utf-8"))
        self.assertNotIn("TEAM STATISTICS (REGULAR SEASON)", standings)
        self.assertIn("team_statistics_page", main)
        self.assertIn("build_team_statistics_page_payload", main)
        self.assertIn("initTeamStatisticsChart", js)
        self.assertIn("initTeamStatisticsFilters", js)
        self.assertIn("initTeamAnalyticsChart", js)
        self.assertIn(".team-statistics-page", css)

    def test_team_statistics_service_imports(self) -> None:
        from app.services.team_statistics import (
            TABLE_COLUMNS,
            build_team_statistics_chart_archive,
            build_team_statistics_page_payload,
            format_rate_value,
        )

        self.assertTrue(len(TABLE_COLUMNS) > 5)
        self.assertEqual(format_rate_value("gf", 82, gp=41, rate="per_game"), 2.0)
        self.assertEqual(format_rate_value("gf", 82, gp=41, rate="per_82"), 164.0)
        self.assertIsNotNone(build_team_statistics_page_payload)
        self.assertIsNotNone(build_team_statistics_chart_archive)

    @patch("app.services.team_statistics.build_team_analytics_chart_archive")
    def test_build_team_statistics_chart_archive_skips_rollover_keys(
        self, archive_mock: MagicMock
    ) -> None:
        from app.services.team_statistics import build_team_statistics_chart_archive

        archive_mock.return_value = {
            "metrics": [],
            "datasets": {
                "1|rs": {
                    "teams": [
                        {"team_id": 10, "metrics": {"gf": 100}},
                    ],
                },
                "y:1969|rs": {
                    "teams": [
                        {"team_id": 20, "metrics": {"gf": 80}},
                    ],
                },
            },
        }
        session = MagicMock()
        session.scalars.return_value.all.return_value = []

        out = build_team_statistics_chart_archive(session, default_season_id=1, default_segment="rs")

        self.assertIn("y:1969|rs", out["datasets"])
        self.assertEqual(out["datasets"]["y:1969|rs"]["teams"][0]["metrics"], {"gf": 80})
        self.assertIn("cf_pct", out["datasets"]["1|rs"]["teams"][0]["metrics"])


if __name__ == "__main__":
    unittest.main()
