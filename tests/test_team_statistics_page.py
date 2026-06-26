"""Tests for Team Statistics page."""
from __future__ import annotations

import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
