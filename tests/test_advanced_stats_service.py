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
    _team_player_trend_game_meta,
    _team_stats_regular_game_limit,
    build_player_process_profile,
    build_team_analytics_chart_archive,
    build_team_player_analytics_archive,
    build_team_player_trends_archive,
    build_team_stats_trends_archive,
    _strength_situation_bucket,
    _team_stats_all_situation_counts,
    _team_stats_situation_goal_counts,
    _goalie_trend_game_counts,
    _skater_player_chart_metrics,
    _skater_trend_game_counts,
    pdo_band,
    sq_profile_from_counts,
    zone_start_pcts,
)


class AdvancedStatsServiceTest(unittest.TestCase):
    def test_team_stats_regular_game_limit_uses_official_gp_and_caps_82(self) -> None:
        self.assertEqual(_team_stats_regular_game_limit(SimpleNamespace(gp=51), 82), 51)
        self.assertEqual(_team_stats_regular_game_limit(SimpleNamespace(gp=90), 90), 82)
        self.assertEqual(_team_stats_regular_game_limit(None, 60), 60)

    def test_strength_situation_bucket(self) -> None:
        self.assertEqual(_strength_situation_bucket("5 on 5"), "ev")
        self.assertEqual(_strength_situation_bucket("PP"), "pp")
        self.assertEqual(_strength_situation_bucket("SH"), "pk")

    def test_team_stats_all_situation_counts(self) -> None:
        game = SimpleNamespace(
            home_team_id=1,
            away_team_id=2,
            home_score=3,
            away_score=1,
            home_shots=30,
            away_shots=25,
            pp_goals_home=1,
            pp_opp_home=4,
            pp_goals_away=0,
            pp_opp_away=2,
            pim_home=8,
            pim_away=6,
            hits_home=12,
            hits_away=10,
            sq3_home=2,
            sq4_home=1,
            sq3_away=1,
            sq4_away=0,
            went_to_overtime=False,
            went_to_shootout=False,
        )
        out = _team_stats_all_situation_counts(game, 1)
        self.assertEqual(out["gf"], 3)
        self.assertEqual(out["ga"], 1)
        self.assertEqual(out["goal_diff"], 2)
        self.assertEqual(out["hd_for"], 3)

    def test_team_stats_all_situation_counts_preserves_missing_optional_data(self) -> None:
        game = SimpleNamespace(
            home_team_id=1,
            away_team_id=2,
            home_score=2,
            away_score=1,
            home_shots=None,
            away_shots=None,
            pp_goals_home=None,
            pp_opp_home=None,
            pp_goals_away=None,
            pp_opp_away=None,
            pim_home=None,
            pim_away=None,
            hits_home=None,
            hits_away=None,
            sq3_home=None,
            sq4_home=None,
            sq3_away=None,
            sq4_away=None,
            went_to_overtime=False,
            went_to_shootout=False,
        )
        out = _team_stats_all_situation_counts(game, 1)
        self.assertEqual(out["goal_diff"], 1)
        self.assertIsNone(out["shot_diff"])
        self.assertIsNone(out["pp_opp"])
        self.assertIsNone(out["hits_diff"])
        self.assertIsNone(out["hd_for"])

    def test_team_stats_situation_goal_counts(self) -> None:
        game = SimpleNamespace(home_team_id=5, away_team_id=6)
        events = [
            SimpleNamespace(scorer_player_id=1, scoring_team_id=5, strength="5 on 5"),
            SimpleNamespace(scorer_player_id=2, scoring_team_id=6, strength="PP"),
        ]
        out = _team_stats_situation_goal_counts(game, 5, "ev", events)
        self.assertEqual(out["gf"], 1)
        self.assertEqual(out["ga"], 0)

    def test_build_team_stats_trends_archive_sparse(self) -> None:
        session = MagicMock()
        team = SimpleNamespace(id=3, full_display_name=lambda: "Test Team")

        def scalars_side_effect(_stmt):
            mock = MagicMock()
            mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        out = build_team_stats_trends_archive(session, team, default_season_id=1, default_segment="rs")
        self.assertEqual(out["seasons"], [])
        self.assertEqual(out["default_situation"], "all")
        self.assertEqual(out["rs_game_cap"], 82)
        self.assertEqual(out["datasets"], {})

    def test_team_player_trend_game_meta_reindexes_line_games(self) -> None:
        games = [
            SimpleNamespace(id=10, game_date=None),
            SimpleNamespace(id=20, game_date=None),
            SimpleNamespace(id=30, game_date=None),
        ]
        out = _team_player_trend_game_meta(games, {20, 30})
        self.assertNotIn(10, out)
        self.assertEqual(out[20]["game_number"], 1)
        self.assertEqual(out[30]["game_number"], 2)

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
        # (0*10 + 1*10 + 2*10 + 3*15 + 4*5) / 50 = 95/50 = 1.9
        self.assertEqual(prof["sq_avg"], 1.9)

    def test_sq_profile_empty_has_null_avg(self) -> None:
        prof = sq_profile_from_counts({})
        self.assertEqual(prof["total"], 0)
        self.assertIsNone(prof["sq_avg"])
        self.assertIsNone(prof["high_danger_share"])

    def test_filter_archived_line_rows(self) -> None:
        from app.services.advanced_stats import filter_archived_line_rows

        rows = [
            {
                "team": SimpleNamespace(id=1, name="A"),
                "line_type": "Forward",
                "combined_gp": 40,
                "combined_toi_seconds": 5000,
            },
            {
                "team": SimpleNamespace(id=2, name="B"),
                "line_type": "Defense",
                "combined_gp": 10,
                "combined_toi_seconds": 1000,
            },
        ]
        out = filter_archived_line_rows(rows, team_id=1, line_type="forward", min_combined_gp=20)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["team"].id, 1)

    def test_hydrate_advanced_stats_hub_from_json_replaces_team_dicts(self) -> None:
        from app.services.advanced_stats import hydrate_advanced_stats_hub_from_json

        team = SimpleNamespace(id=7, name="Isles", abbreviation="NYI", slug="ny-islanders")
        session = MagicMock()
        session.scalars.return_value.all.return_value = [team]
        hub = hydrate_advanced_stats_hub_from_json(
            session,
            {
                "skaters": [{"player_id": 1, "team": {"id": 7, "name": "Isles", "slug": "ny-islanders"}}],
                "goalies": [],
                "teams": [],
                "luck": [],
                "discipline": [],
                "shot_quality": [],
                "lines": [],
                "points_above_ppg": [],
            },
        )
        self.assertIs(hub["skaters"][0]["team"], team)

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

    def test_skater_player_chart_metrics_shape(self) -> None:
        st = SimpleNamespace(
            player_id=9,
            gp=20,
            goals=15,
            assists=20,
            points=35,
            shots=120,
            toi_seconds=72000,
            cf_pct=52.0,
            ff_pct=51.0,
            cf=100,
            ca=90,
            ff=80,
            fa=75,
            cf_pct_rel=None,
            ff_pct_rel=None,
            sf_per_60=8.0,
            sa_per_60=7.5,
            gf_per_60=3.0,
            ga_per_60=2.8,
            ppto_seconds=1800,
            ppg=2,
            pp_assists=3,
            shto_seconds=600,
            shg=0,
            sh_assists=1,
            pdo=100.5,
            blocked_shots=10,
            hits=20,
            takeaways=5,
            giveaways=4,
            faceoff_wins=100,
            faceoffs=200,
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            SimpleNamespace(sq0=1, sq1=1, sq2=1, sq3=2, sq4=1)
        ]
        out = _skater_player_chart_metrics(session, st, season_id=1)
        self.assertEqual(out["points"], 35)
        self.assertEqual(out["pts_per_60"], 1.75)
        self.assertEqual(out["high_danger_share"], 50.0)

    def test_skater_trend_game_counts(self) -> None:
        line = SimpleNamespace(
            goals=2,
            assists=1,
            shots=5,
            pim=4,
            hits=3,
            blocked_shots=1,
            missed_shots=2,
            takeaways=1,
            giveaways=0,
            sq0=1,
            sq1=1,
            sq2=1,
            sq3=2,
            sq4=1,
            team_shots_off=12,
            team_shots_against_off=8,
        )
        out = _skater_trend_game_counts(line)
        self.assertEqual(out["points"], 3)
        self.assertEqual(out["high_danger_attempts"], 3)
        self.assertEqual(out["sq_total"], 6)
        self.assertEqual(out["team_shots_total"], 20)

    def test_goalie_trend_game_counts(self) -> None:
        line = SimpleNamespace(
            saves=28,
            shots_against=30,
            goals_allowed=2,
            toi_seconds=3600,
            game_rating=7.5,
        )
        out = _goalie_trend_game_counts(line)
        self.assertEqual(out["saves"], 28)
        self.assertEqual(out["sa"], 30)
        self.assertEqual(out["ga"], 2)
        self.assertEqual(out["game_rating"], 7.5)

    def test_build_team_player_trends_archive_sparse(self) -> None:
        session = MagicMock()
        team = SimpleNamespace(id=3, full_display_name=lambda: "Test Team")

        def scalars_side_effect(_stmt):
            mock = MagicMock()
            mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        out = build_team_player_trends_archive(session, team, default_season_id=1, default_segment="rs")
        self.assertEqual(out["seasons"], [])
        self.assertEqual(out["default_kind"], "skater")
        self.assertEqual(out["default_metric_skater"], "goals")
        self.assertEqual(out["datasets"], {})

    @patch("app.services.season_team_logo_bundle.get_season_team_logo_bundle")
    def test_build_team_player_analytics_archive_sparse(self, logo_bundle_mock: MagicMock) -> None:
        logo_bundle_mock.return_value.team_logo_url_for_season_context.return_value = ""
        session = MagicMock()
        team = SimpleNamespace(id=3, full_display_name=lambda: "Test Team")

        def scalars_side_effect(_stmt):
            mock = MagicMock()
            stmt = str(_stmt)
            if "distinct" in stmt:
                mock.all.return_value = []
            else:
                mock.all.return_value = []
            return mock

        session.scalars.side_effect = scalars_side_effect
        out = build_team_player_analytics_archive(session, team, default_season_id=1, default_segment="rs")
        self.assertEqual(out["seasons"], [])
        self.assertEqual(out["default_kind"], "skater")

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
