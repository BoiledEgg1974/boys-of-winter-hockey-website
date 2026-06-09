"""Standings trend chart service and template tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.standings import (
    RS_GAME_CAP,
    _game_result_lines,
    _standings_trend_gp_limit,
    _standings_trend_title,
    build_standings_trend_chart,
)


class StandingsTrendServiceTest(unittest.TestCase):
    def test_standings_trend_gp_limit_caps_at_82(self) -> None:
        st = SimpleNamespace(gp=90, w=45, l=30, ties=0, shootout_wins=5, shootout_losses=5)
        st.standing_gp_display = lambda: 80
        self.assertEqual(_standings_trend_gp_limit(st), 80)

        st2 = SimpleNamespace(gp=100, w=50, l=32, ties=0, shootout_wins=0, shootout_losses=0)
        st2.standing_gp_display = lambda: 82
        self.assertEqual(_standings_trend_gp_limit(st2), 82)

    def test_game_result_lines_regulation_loss(self) -> None:
        game = SimpleNamespace(
            home_team_id=1,
            away_team_id=2,
            home_score=3,
            away_score=1,
            went_to_overtime=False,
            went_to_shootout=False,
        )
        line1, line2 = _game_result_lines(game, 2, "CGY")
        self.assertEqual(line1, "Loss vs CGY")
        self.assertEqual(line2, "in regulation")

    def test_game_result_lines_overtime_win(self) -> None:
        game = SimpleNamespace(
            home_team_id=1,
            away_team_id=2,
            home_score=4,
            away_score=3,
            went_to_overtime=True,
            went_to_shootout=False,
        )
        line1, line2 = _game_result_lines(game, 1, "EDM")
        self.assertEqual(line1, "Win vs EDM")
        self.assertEqual(line2, "in overtime")

    def test_standings_trend_title_by_view(self) -> None:
        self.assertEqual(
            _standings_trend_title(
                view="overall",
                league_display_name="BOWL Fantasy",
                sel_conference=None,
                sel_division=None,
            ),
            "BOWL Fantasy",
        )
        self.assertEqual(
            _standings_trend_title(
                view="conference",
                league_display_name="BOWL Fantasy",
                sel_conference="East",
                sel_division=None,
            ),
            "East",
        )
        self.assertEqual(
            _standings_trend_title(
                view="division",
                league_display_name="BOWL Fantasy",
                sel_conference=None,
                sel_division="Atlantic",
            ),
            "Atlantic",
        )

    @patch("app.services.season_team_logo_bundle.get_season_team_logo_bundle")
    def test_build_standings_trend_chart_points_above_group_average(self, mock_logo_bundle) -> None:
        mock_logo_bundle.return_value.team_logo_url_for_season_context.return_value = "/static/logo.png"

        team_a = SimpleNamespace(
            id=1,
            slug="a",
            name="Team A",
            abbreviation="AAA",
            primary_color="#ff0000",
            secondary_color=None,
            full_display_name=lambda: "Team A",
        )
        team_b = SimpleNamespace(
            id=2,
            slug="b",
            name="Team B",
            abbreviation="BBB",
            primary_color="#0000ff",
            secondary_color=None,
            full_display_name=lambda: "Team B",
        )
        st_a = SimpleNamespace(team_id=1, team=team_a, gp=2, w=2, l=0, ties=0, shootout_wins=0, shootout_losses=0)
        st_a.standing_gp_display = lambda: 2
        st_b = SimpleNamespace(team_id=2, team=team_b, gp=2, w=0, l=2, ties=0, shootout_wins=0, shootout_losses=0)
        st_b.standing_gp_display = lambda: 2

        games = [
            SimpleNamespace(
                id=10,
                season_id=1,
                game_date=None,
                home_team_id=1,
                away_team_id=2,
                home_score=3,
                away_score=1,
                status="final",
                game_type="regular season",
                went_to_overtime=False,
                went_to_shootout=False,
            ),
            SimpleNamespace(
                id=11,
                season_id=1,
                game_date=None,
                home_team_id=2,
                away_team_id=1,
                home_score=0,
                away_score=2,
                status="final",
                game_type="regular season",
                went_to_overtime=False,
                went_to_shootout=False,
            ),
        ]

        session = MagicMock()
        session.scalars.return_value.all.return_value = games

        out = build_standings_trend_chart(
            session,
            1,
            [st_a, st_b],
            view="overall",
            logo_season_year=2025,
            league_display_name="Test League",
        )

        self.assertEqual(out["title"], "Test League")
        self.assertEqual(out["rs_game_cap"], RS_GAME_CAP)
        self.assertEqual(len(out["teams"]), 2)
        team_map = {t["team_id"]: t for t in out["teams"]}
        self.assertEqual(team_map[1]["points"][-1]["points_total"], 4)
        self.assertEqual(team_map[2]["points"][-1]["points_total"], 0)
        self.assertEqual(team_map[1]["points"][-1]["gp"], 2)
        self.assertEqual(team_map[1]["points"][-1]["value"], 2.0)
        self.assertEqual(team_map[2]["points"][-1]["value"], -2.0)
        self.assertEqual(team_map[1]["points"][-1]["result_line1"], "Win vs BBB")
        self.assertEqual(team_map[2]["points"][0]["result_line2"], "in regulation")

    def test_build_standings_trend_chart_empty_without_season(self) -> None:
        session = MagicMock()
        out = build_standings_trend_chart(session, None, [])
        self.assertEqual(out["teams"], [])
        self.assertEqual(out["max_gp"], 0)


class StandingsTrendTemplateTest(unittest.TestCase):
    def test_standings_template_markers(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "standings.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")

        self.assertIn("standings-trends", template)
        self.assertIn("standings-trends-data", template)
        self.assertIn("data-league-logo-url", template)
        idx_chart = template.index("standings-trends")
        idx_stats = template.index('team_stats_panel("TEAM STATISTICS (REGULAR SEASON)"')
        self.assertLess(idx_chart, idx_stats)
        self.assertIn("build_standings_trend_chart", main)
        self.assertIn("standings_trend_chart=standings_trend_chart", main)
        self.assertIn("initStandingsTrendCharts", js)
        self.assertIn("standings-trends__watermark", js)
        self.assertIn("standings-trends__tooltip-line--result", js)
        self.assertIn(".standings-trends__line", css)
        self.assertIn(".standings-trends__watermark", css)
        self.assertIn(".standings-trends__tooltip", css)


if __name__ == "__main__":
    unittest.main()
