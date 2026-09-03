"""GM achievement detectors, catalog filters, and watermark award rules."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Team
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS
from app.services.gm_achievements import (
    ACHIEVEMENT_UNLOCKED_EVENT_KEY,
    build_playoff_series,
    catalog_for_league,
    collect_new_hits,
    detect_goalie_win_1_0_40,
    detect_gordie_howe,
    detect_natural_hat_trick,
    evaluate_gm_achievements_after_import,
    is_fighting_infraction,
    unlock_source_ref,
)
from app.site_models import GmAchievementUnlock, GmAchievementWatermark, GmLeagueMembership, User
from sqlalchemy import delete, select


class DetectorTests(unittest.TestCase):
    def test_gordie_howe_needs_goal_assist_and_fight(self) -> None:
        self.assertTrue(detect_gordie_howe(goals=1, assists=1, fought=True))
        self.assertFalse(detect_gordie_howe(goals=2, assists=1, fought=False))
        self.assertFalse(detect_gordie_howe(goals=0, assists=1, fought=True))
        self.assertTrue(is_fighting_infraction("Fighting"))
        self.assertFalse(is_fighting_infraction("Hooking"))

    def test_natural_hat_trick_three_in_a_row(self) -> None:
        events = [
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="2:00"),
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="5:10"),
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="12:40"),
            SimpleNamespace(scorer_player_id=3, period=2, time_elapsed="1:00"),
        ]
        self.assertEqual(detect_natural_hat_trick(events), 9)
        broken = [
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="2:00"),
            SimpleNamespace(scorer_player_id=4, period=1, time_elapsed="3:00"),
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="4:00"),
            SimpleNamespace(scorer_player_id=9, period=1, time_elapsed="5:00"),
        ]
        self.assertIsNone(detect_natural_hat_trick(broken))

    def test_goalie_win_1_0_forty_shots(self) -> None:
        self.assertTrue(
            detect_goalie_win_1_0_40(
                home_score=1, away_score=0, home_shots=18, away_shots=41, team_is_home=True
            )
        )
        self.assertFalse(
            detect_goalie_win_1_0_40(
                home_score=1, away_score=0, home_shots=18, away_shots=22, team_is_home=True
            )
        )
        self.assertTrue(
            detect_goalie_win_1_0_40(
                home_score=0, away_score=1, home_shots=44, away_shots=12, team_is_home=False
            )
        )

    def test_playoff_upset_and_guarantee(self) -> None:
        games = []
        # Lower seed 2 (team 20) beats higher seed 1 (team 10) 4-3 after trailing 3-2
        winners = [10, 10, 10, 20, 20, 20, 20]
        for i, wid in enumerate(winners, start=1):
            home, away = (10, 20) if wid == 10 else (20, 10)
            games.append(
                SimpleNamespace(
                    id=i,
                    game_date=None,
                    home_team_id=home,
                    away_team_id=away,
                    home_score=3,
                    away_score=1,
                )
            )
        series = build_playoff_series(games, {10: 1, 20: 8})
        self.assertEqual(len(series), 1)
        ser = series[0]
        self.assertEqual(ser.winner_id, 20)
        self.assertTrue(ser.is_upset())
        self.assertTrue(ser.trailed_3_2_then_won())
        self.assertFalse(ser.is_sweep)
        self.assertTrue(ser.winner_is_lowest_seed({10: 1, 20: 8}))


class CatalogTests(unittest.TestCase):
    def test_going_up_is_relegation_only(self) -> None:
        cap_keys = {item.key for item in catalog_for_league("bowl-cap")}
        hist_keys = {item.key for item in catalog_for_league("bowl-historical")}
        rel_keys = {item.key for item in catalog_for_league("bowl-fantasy")}
        self.assertNotIn("going_up", cap_keys)
        self.assertNotIn("going_up", hist_keys)
        self.assertIn("going_up", rel_keys)
        self.assertIn("pinnacle", cap_keys)

    def test_source_ref_and_new_hits(self) -> None:
        self.assertEqual(unlock_source_ref("bowl-cap", 8, "pinnacle"), "gm_ach:bowl-cap:8:pinnacle")
        truths = {8: {"pinnacle": {"d": 1}, "gordie_howe": {"d": 2}}}
        first = collect_new_hits(truths, set(), set())
        self.assertEqual({(t, k) for t, k, _m in first}, {(8, "pinnacle"), (8, "gordie_howe")})
        already = {(8, "pinnacle")}
        second = collect_new_hits(truths, already, set())
        self.assertEqual([(t, k) for t, k, _m in second], [(8, "gordie_howe")])
        third = collect_new_hits(truths, already, {(8, "gordie_howe")})
        self.assertEqual(third, [])

    def test_discord_route_seeded(self) -> None:
        self.assertIn(ACHIEVEMENT_UNLOCKED_EVENT_KEY, DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[ACHIEVEMENT_UNLOCKED_EVENT_KEY], "achievements")


class EvaluatorWatermarkTests(unittest.TestCase):
    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        with app.app_context():
            db.session.rollback()
            db.session.remove()

    def test_first_run_seeds_without_ap_second_awards_once(self) -> None:
        self.app = create_app(make_league_config("bowl-cap"))
        with self.app.app_context():
            team = db.session.scalar(select(Team).order_by(Team.id).limit(1))
            self.assertIsNotNone(team)
            tid = int(team.id)
            user = User(
                email="gm-ach-test@example.invalid",
                password_hash="x",
                discord_name="ACH Test",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                GmLeagueMembership(
                    league_slug="bowl-cap",
                    user_id=int(user.id),
                    team_id=tid,
                    status="active",
                )
            )
            existing_mark = db.session.scalar(
                select(GmAchievementWatermark).where(
                    GmAchievementWatermark.league_slug == "bowl-cap"
                ).limit(1)
            )
            if existing_mark is not None:
                db.session.delete(existing_mark)
            db.session.flush()

            def _flush_only(session) -> None:
                session.flush()

            truths_seed = {tid: {"pinnacle": {"detail": "already"}}}
            truths_next = {
                tid: {
                    "pinnacle": {"detail": "already"},
                    "gordie_howe": {"detail": "new"},
                }
            }
            with (
                patch(
                    "app.services.gm_achievements.discover_true_achievements",
                    side_effect=[truths_seed, truths_next, truths_next],
                ),
                patch("app.sqlite_retry.commit_with_sqlite_retry", _flush_only),
                patch("app.services.gm_achievements._enqueue_achievement_discord"),
            ):
                first = evaluate_gm_achievements_after_import(self.app)
                self.assertEqual(first["seeded"], 1)
                self.assertEqual(first["awarded"], 0)
                unlocks = list(
                    db.session.scalars(
                        select(GmAchievementUnlock).where(
                            GmAchievementUnlock.league_slug == "bowl-cap",
                            GmAchievementUnlock.team_id == tid,
                        )
                    ).all()
                )
                self.assertEqual(unlocks, [])
                mark = db.session.scalar(
                    select(GmAchievementWatermark).where(
                        GmAchievementWatermark.league_slug == "bowl-cap"
                    ).limit(1)
                )
                self.assertIsNotNone(mark)
                self.assertIn("pinnacle", mark.already_true_map().get(str(tid), []))

                second = evaluate_gm_achievements_after_import(self.app)
                self.assertEqual(second["seeded"], 0)
                self.assertEqual(second["awarded"], 1)
                third = evaluate_gm_achievements_after_import(self.app)
                self.assertEqual(third["awarded"], 0)

            db.session.rollback()


if __name__ == "__main__":
    unittest.main()
