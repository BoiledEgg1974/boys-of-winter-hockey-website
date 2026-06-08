"""Advanced stats service unit tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.advanced_stats import (
    _aggregate_game_skater_lines,
    _fo_pct,
    _goalie_game_log_profile,
    _goalie_record_label,
    _goalie_saves,
    _goalie_season_process_snapshot,
    _recent_goalie_window,
    _skater_game_event_profile,
    _skater_season_process_snapshot,
    _team_chart_metric_values,
    build_player_process_profile,
    build_team_analytics_chart_archive,
    pdo_band,
    sq_profile_from_counts,
    zone_start_pcts,
)


class AdvancedStatsServiceTest(unittest.TestCase):
    def test_zone_start_pcts(self) -> None:
        out = zone_start_pcts(40, 20, 40)
        self.assertEqual(out["oz"], 40.0)
        self.assertEqual(out["nz"], 20.0)
        self.assertEqual(out["dz"], 40.0)

    def test_zone_start_pcts_empty(self) -> None:
        out = zone_start_pcts(0, 0, 0)
        self.assertIsNone(out["oz"])

    def test_sq_profile_high_danger_share(self) -> None:
        prof = sq_profile_from_counts({"sq0": 10, "sq1": 10, "sq2": 10, "sq3": 15, "sq4": 5})
        self.assertEqual(prof["total"], 50)
        self.assertEqual(prof["high_danger_share"], 40.0)

    def test_pdo_band(self) -> None:
        self.assertEqual(pdo_band(102.0), "hot")
        self.assertEqual(pdo_band(98.0), "cold")
        self.assertEqual(pdo_band(100.0), "neutral")

    def test_fo_pct_from_wins_and_losses(self) -> None:
        self.assertEqual(_fo_pct(55, 45), 55.0)
        self.assertIsNone(_fo_pct(0, 0))

    def test_fo_pct_from_total_faceoffs(self) -> None:
        self.assertEqual(_fo_pct(400, total=800), 50.0)

    def test_skater_season_process_snapshot_fallbacks(self) -> None:
        st = SimpleNamespace(
            gp=20,
            cf_pct=None,
            ff_pct=None,
            cf=55,
            ca=45,
            ff=50,
            fa=50,
            cf_pct_rel=3.2,
            ff_pct_rel=1.1,
            sf_per_60=8.5,
            sa_per_60=7.2,
            gf_per_60=3.1,
            ga_per_60=2.4,
            toi_seconds=72000,
            points=40,
            ppto_seconds=3600,
            ppg=6,
            pp_assists=2,
            shto_seconds=1800,
            shg=1,
            sh_assists=1,
            pdo=101.2,
            shots=120,
            blocked_shots=18,
            hits=42,
            takeaways=11,
            giveaways=9,
            faceoff_wins=220,
            faceoffs=400,
        )
        out = _skater_season_process_snapshot(st)
        self.assertEqual(out["cf_pct"], 55.0)
        self.assertEqual(out["ff_pct"], 50.0)
        self.assertEqual(out["pts_per_60"], 2.0)
        self.assertEqual(out["pp_pts_per_60"], 8.0)
        self.assertEqual(out["sh_pts_per_60"], 4.0)
        self.assertEqual(out["fo_pct"], 55.0)
        self.assertEqual(out["pdo_band"], "hot")

    def test_skater_game_event_profile_aggregates(self) -> None:
        lines = [
            SimpleNamespace(
                shots=4,
                missed_shots=2,
                blocked_shots=1,
                hits=3,
                takeaways=1,
                giveaways=0,
                faceoffs_won=6,
                faceoffs_lost=4,
            ),
            SimpleNamespace(
                shots=2,
                missed_shots=1,
                blocked_shots=0,
                hits=1,
                takeaways=0,
                giveaways=2,
                faceoffs_won=4,
                faceoffs_lost=6,
            ),
        ]
        out = _skater_game_event_profile(lines)
        self.assertEqual(out["sog"], 6)
        self.assertEqual(out["missed_shots"], 3)
        self.assertEqual(out["hits"], 4)
        self.assertEqual(out["fo_pct"], 50.0)

    def test_aggregate_game_skater_lines_includes_points_and_hd_share(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            SimpleNamespace(
                goals=2,
                assists=1,
                shots=5,
                toi_seconds=1200,
                oz_starts=1,
                nz_starts=0,
                dz_starts=0,
                sq0=1,
                sq1=1,
                sq2=1,
                sq3=1,
                sq4=1,
            )
        ]
        out = _aggregate_game_skater_lines(session, player_id=7, game_ids=[101])
        self.assertEqual(out["goals"], 2)
        self.assertEqual(out["assists"], 1)
        self.assertEqual(out["points"], 3)
        self.assertEqual(out["sf_per_60"], 15.0)
        self.assertEqual(out["high_danger_share"], 40.0)

    def test_build_player_process_profile_returns_none_without_season_row(self) -> None:
        session = MagicMock()
        session.scalars.return_value.first.return_value = None
        player = SimpleNamespace(id=1)
        self.assertIsNone(
            build_player_process_profile(session, player, season_id=1, is_goalie=False)
        )

    def test_goalie_saves_and_record_helpers(self) -> None:
        st = SimpleNamespace(sa=100, ga=10, saves=0)
        self.assertEqual(_goalie_saves(st), 90)
        rec = SimpleNamespace(wins=20, losses=10, otl=5)
        self.assertEqual(_goalie_record_label(rec), "20-10-5")

    def test_goalie_season_process_snapshot(self) -> None:
        st = SimpleNamespace(
            gp=30,
            games_started=28,
            minutes_played=1750,
            wins=18,
            losses=8,
            otl=4,
            sa=900,
            ga=75,
            so=3,
            sv_pct=0.917,
            gaa=2.45,
            game_rating=7.2,
            gsaa=4.5,
        )
        out = _goalie_season_process_snapshot(st, league_sv_pct=0.905)
        self.assertEqual(out["record"], "18-8-4")
        self.assertEqual(out["saves"], 825)
        self.assertEqual(out["gsaa"], 4.5)
        self.assertFalse(out["gsaa_estimated"])

    def test_goalie_game_log_profile(self) -> None:
        lines = [
            SimpleNamespace(game_rating=7.0, toi_seconds=3600, goals_allowed=0, shots_against=30),
            SimpleNamespace(game_rating=8.0, toi_seconds=3600, goals_allowed=2, shots_against=28),
        ]
        out = _goalie_game_log_profile(lines)
        self.assertEqual(out["gp"], 2)
        self.assertEqual(out["shutouts"], 1)
        self.assertEqual(out["avg_game_rating"], 7.5)

    def test_recent_goalie_window_includes_rating_and_shutouts(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            SimpleNamespace(
                shots_against=30,
                goals_allowed=0,
                saves=30,
                toi_seconds=3600,
                game_rating=8.0,
            ),
            SimpleNamespace(
                shots_against=28,
                goals_allowed=3,
                saves=25,
                toi_seconds=3600,
                game_rating=6.0,
            ),
        ]
        out = _recent_goalie_window(session, player_id=3, window=10)
        self.assertEqual(out["gp"], 2)
        self.assertEqual(out["shutouts"], 1)
        self.assertEqual(out["avg_game_rating"], 7.0)

    def test_build_player_process_profile_goalie_payload(self) -> None:
        st = SimpleNamespace(
            gp=10,
            games_started=9,
            minutes_played=600,
            wins=6,
            losses=3,
            otl=1,
            sa=300,
            ga=25,
            so=1,
            sv_pct=0.917,
            gaa=2.5,
            game_rating=None,
            gsaa=None,
        )
        game_line = SimpleNamespace(
            game_rating=7.5,
            toi_seconds=3600,
            goals_allowed=2,
            shots_against=30,
        )
        session = MagicMock()
        scalars_calls = {"n": 0}

        def scalars_side_effect(_stmt):
            scalars_calls["n"] += 1
            mock = MagicMock()
            if scalars_calls["n"] == 1:
                mock.first.return_value = st
            elif scalars_calls["n"] == 2:
                mock.all.return_value = [st]
            elif scalars_calls["n"] == 3:
                mock.all.return_value = [game_line]
            else:
                mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        player = SimpleNamespace(id=11)
        out = build_player_process_profile(session, player, season_id=2, is_goalie=True)
        self.assertIsNotNone(out)
        self.assertEqual(out["kind"], "goalie")
        self.assertEqual(out["season"]["record"], "6-3-1")
        self.assertIn("game_log", out)
        self.assertTrue(out["season"]["gsaa_estimated"])

    def test_team_chart_metric_values_include_standings_fields(self) -> None:
        out = _team_chart_metric_values(
            {
                "gp": 20,
                "gf": 60,
                "ga": 50,
                "shots_for": 600,
                "shots_against": 580,
                "shot_diff": 20,
                "pp_pct": 22.5,
                "pk_pct": 81.0,
                "sq_high_danger": 34.0,
                "pts": 52,
            }
        )
        self.assertEqual(out["goal_diff"], 10)
        self.assertEqual(out["point_pct"], 130.0)
        self.assertEqual(out["points_above_ppg"], 32)

    @patch("app.services.season_team_logo_bundle.get_season_team_logo_bundle")
    def test_build_team_analytics_chart_archive_sparse(self, logo_bundle_mock: MagicMock) -> None:
        logo_bundle_mock.return_value.team_logo_url_for_season_context.return_value = ""
        session = MagicMock()

        def scalars_side_effect(_stmt):
            mock = MagicMock()
            stmt = str(_stmt)
            if "distinct" in stmt:
                mock.all.return_value = []
            else:
                mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        out = build_team_analytics_chart_archive(session, default_season_id=1, default_segment="rs")
        self.assertEqual(out["seasons"], [])
        self.assertEqual(out["datasets"], {})
        self.assertEqual(out["default_x"], "gf")

    def test_build_player_process_profile_sparse_skater_payload(self) -> None:
        st = SimpleNamespace(
            gp=0,
            cf_pct=None,
            ff_pct=None,
            cf=None,
            ca=None,
            ff=None,
            fa=None,
            cf_pct_rel=None,
            ff_pct_rel=None,
            sf_per_60=None,
            sa_per_60=None,
            gf_per_60=None,
            ga_per_60=None,
            toi_seconds=None,
            points=None,
            ppto_seconds=None,
            pp_points=None,
            shto_seconds=None,
            sh_points=None,
            pdo=None,
            shots=None,
            blocked_shots=None,
            hits=None,
            takeaways=None,
            giveaways=None,
            faceoff_wins=None,
            faceoffs=None,
        )
        session = MagicMock()
        scalars_calls = {"n": 0}

        def scalars_side_effect(_stmt):
            scalars_calls["n"] += 1
            mock = MagicMock()
            if scalars_calls["n"] == 1:
                mock.first.return_value = st
            else:
                mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        session.scalar.return_value = 0
        session.execute.return_value.all.return_value = []
        player = SimpleNamespace(id=5)
        out = build_player_process_profile(session, player, season_id=9, is_goalie=False)
        self.assertIsNotNone(out)
        self.assertEqual(out["kind"], "skater")
        self.assertIn("shot_share", out)
        self.assertIn("game_events", out)
        self.assertEqual(out["rolling"]["last_10"], {})
        self.assertIsNone(out["season"]["cf_pct"])
        self.assertIsNone(out["season"]["pts_per_60"])


if __name__ == "__main__":
    unittest.main()
