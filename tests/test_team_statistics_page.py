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
        self.assertIn('name="season"', template)
        self.assertIn("build_team_statistics_season_options", main)

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

    @patch("app.services.team_records.all_year_labels_desc")
    @patch("app.services.analytics_snapshots.load_team_rollover_years")
    @patch("app.services.team_statistics._seasons_with_team_data")
    def test_season_options_catalog_live_archive_and_records(
        self,
        live_mock: MagicMock,
        rollover_mock: MagicMock,
        records_mock: MagicMock,
    ) -> None:
        from types import SimpleNamespace

        from app.services.team_statistics import (
            build_team_statistics_season_options,
            resolve_team_statistics_season_option,
        )

        live_mock.return_value = [SimpleNamespace(id=1, start_year=1971, label="1971-72")]
        rollover_mock.return_value = [1970]
        records_mock.return_value = ["1971-72", "1970-71", "1968-69", "1668-69"]

        options = build_team_statistics_season_options(MagicMock())
        keys = [opt["key"] for opt in options]
        self.assertEqual(keys, ["s:1", "y:1970", "y:1968"])
        self.assertEqual(options[0]["source"], "live")
        self.assertEqual(options[1]["source"], "archive")
        self.assertEqual(options[2]["source"], "records")

        live = SimpleNamespace(id=1, start_year=1971, label="1971-72")
        self.assertEqual(
            resolve_team_statistics_season_option(options, season_key="y:1968", live_season=live)["label"],
            "1968-69",
        )
        self.assertEqual(
            resolve_team_statistics_season_option(options, season_id=1, live_season=live)["key"],
            "s:1",
        )

    def test_table_row_from_record_maps_core_stats(self) -> None:
        from types import SimpleNamespace

        from app.services.team_statistics import _table_row_from_record

        team = SimpleNamespace(
            id=7,
            slug="bos",
            name="Boston Bruins",
            abbreviation="BOS",
            primary_color="#000",
            full_display_name=lambda: "Boston Bruins",
        )
        rec = SimpleNamespace(
            gp=78,
            w=40,
            l=30,
            pts=88,
            gf=250,
            ga=200,
            goal_diff=50,
            shots_for=2400,
            shots_against=2200,
            ppg=50,
            pp_chances=250,
            pp_pct=20.0,
            ppg_against=40,
            sh_chances=240,
            pk_pct=83.3,
            shg=6,
            pim_per_game=12.5,
        )
        row = _table_row_from_record(rec, team=team, display_name="Boston Bruins", logo_url="/bos.png")
        self.assertEqual(row["gf"], 250)
        self.assertEqual(row["diff"], 50)
        self.assertEqual(row["shot_diff"], 200)
        self.assertEqual(row["pp_pct"], 20.0)
        self.assertEqual(row["point_pct"], 56.4)

    @patch("app.services.season_team_logo_bundle.get_season_team_logo_bundle")
    @patch("app.services.team_records.team_display_name", return_value="Montreal Canadiens")
    @patch("app.services.team_records._records_have_displayable_standings", return_value=True)
    @patch("app.services.team_records._load_all_records")
    def test_chart_archive_appends_record_only_years(
        self,
        load_mock: MagicMock,
        _standings_mock: MagicMock,
        _name_mock: MagicMock,
        logo_mock: MagicMock,
    ) -> None:
        from types import SimpleNamespace

        from app.services.team_statistics import _append_record_years_to_chart_archive

        team = SimpleNamespace(
            id=3,
            slug="mtl",
            name="Canadiens",
            abbreviation="MTL",
            primary_color="#e00",
        )
        rec = SimpleNamespace(
            start_year=1968,
            season_year_label="1968-69",
            team=team,
            team_id=3,
            gp=76,
            gf=210,
            ga=180,
            goal_diff=30,
            pts=90,
            shots_for=None,
            shots_against=None,
            ppg=None,
            pp_chances=None,
            pp_pct=None,
            ppg_against=None,
            sh_chances=None,
            pk_pct=None,
        )
        load_mock.return_value = [rec]
        logo_mock.return_value.team_logo_url_for_season_context.return_value = "/mtl.png"

        archive = {
            "seasons": [{"id": 1, "label": "1971-72", "start_year": 1971}],
            "datasets": {},
        }
        out = _append_record_years_to_chart_archive(MagicMock(), archive)
        self.assertIn("y:1968|rs", out["datasets"])
        self.assertEqual(out["datasets"]["y:1968|rs"]["teams"][0]["metrics"]["gf"], 210)
        self.assertTrue(any(s.get("id") == "y:1968" for s in out["seasons"]))


if __name__ == "__main__":
    unittest.main()
