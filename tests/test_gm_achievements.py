"""GM achievement detectors, catalog filters, and watermark award rules."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import render_template

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Team
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS
from datetime import date

from app.services.gm_achievements import (
    ACHIEVEMENT_LEAGUE_FIRST_EVENT_KEY,
    ACHIEVEMENT_UNLOCKED_EVENT_KEY,
    CATALOG_BY_KEY,
    acquired_by_team_from_ledger,
    build_achievement_leaderboard,
    build_achievement_rival_page,
    build_achievements_page_payload,
    build_playoff_series,
    catalog_for_league,
    catalog_key_from_storage,
    claim_achievement_scratch,
    collect_new_hits,
    credit_achievement_ap,
    detect_comeback_from_events,
    detect_comeback_from_period_scores,
    detect_consecutive_playoff_shutouts,
    detect_goalie_win_1_0_40,
    detect_gordie_howe,
    detect_heist,
    detect_natural_hat_trick,
    detect_playoff_ot_winner,
    detect_road_win_after_dropping_first_two,
    discover_true_achievements,
    evaluate_gm_achievements_after_import,
    expand_legacy_pairs,
    export_streak_len,
    format_export_recap,
    is_calder_award,
    is_fighting_infraction,
    major_award_slot,
    max_win_streak,
    month_undefeated,
    parse_reward_cells,
    place_label,
    player_ids_from_drag_keys,
    playoff_spot_cutoff,
    rewrite_truths_to_storage,
    roll_reward_cell,
    roll_reward_cells,
    start_achievement_scratch,
    storage_key_for,
    sync_achievement_ap_ledger,
    unclaimed_unlock_count,
    unlock_source_ref,
)
from app.site_models import (
    ApLedgerEntry,
    GmAchievementUnlock,
    GmAchievementWatermark,
    GmLeagueMembership,
    User,
)
from sqlalchemy import delete, select


class _SeqRng:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)
        self.i = 0

    def random(self) -> float:
        if self.i >= len(self.values):
            raise AssertionError("random() called more times than scripted")
        value = self.values[self.i]
        self.i += 1
        return value


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess.clear()
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


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

    def test_reverse_sweep_after_down_0_3(self) -> None:
        winners = [10, 10, 10, 20, 20, 20, 20]
        games = []
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
        ser = build_playoff_series(games, {10: 1, 20: 8})[0]
        self.assertTrue(ser.trailed_0_3_then_won())
        self.assertEqual(ser.winner_id, 20)

    def test_comeback_from_scoring_events(self) -> None:
        events = [
            SimpleNamespace(scoring_team_id=2, period=1, time_elapsed="1:00"),
            SimpleNamespace(scoring_team_id=2, period=1, time_elapsed="2:00"),
            SimpleNamespace(scoring_team_id=2, period=1, time_elapsed="3:00"),
            SimpleNamespace(scoring_team_id=1, period=2, time_elapsed="1:00"),
            SimpleNamespace(scoring_team_id=1, period=2, time_elapsed="2:00"),
            SimpleNamespace(scoring_team_id=1, period=3, time_elapsed="1:00"),
            SimpleNamespace(scoring_team_id=1, period=3, time_elapsed="2:00"),
        ]
        self.assertTrue(
            detect_comeback_from_events(
                events, home_team_id=1, away_team_id=2, winner_id=1, deficit=3
            )
        )
        close_game = [
            SimpleNamespace(scoring_team_id=2, period=1, time_elapsed="1:00"),
            SimpleNamespace(scoring_team_id=2, period=1, time_elapsed="2:00"),
            SimpleNamespace(scoring_team_id=1, period=2, time_elapsed="1:00"),
            SimpleNamespace(scoring_team_id=1, period=2, time_elapsed="2:00"),
            SimpleNamespace(scoring_team_id=1, period=3, time_elapsed="1:00"),
        ]
        self.assertFalse(
            detect_comeback_from_events(
                close_game, home_team_id=1, away_team_id=2, winner_id=1, deficit=3
            )
        )

    def test_comeback_from_period_scores(self) -> None:
        game = SimpleNamespace(
            home_team_id=1,
            away_team_id=2,
            score_home_p1=0,
            score_home_p2=2,
            score_home_p3=2,
            score_home_ot=None,
            score_away_p1=3,
            score_away_p2=0,
            score_away_p3=0,
            score_away_ot=None,
        )
        self.assertTrue(detect_comeback_from_period_scores(game, winner_id=1))
        self.assertFalse(detect_comeback_from_period_scores(game, winner_id=2))

    def test_playoff_ot_winner_uses_period_four(self) -> None:
        game = SimpleNamespace(game_type="playoff", went_to_overtime=True)
        events = [
            SimpleNamespace(scorer_player_id=8, scoring_team_id=1, period=3, time_elapsed="19:50"),
            SimpleNamespace(scorer_player_id=9, scoring_team_id=1, period=4, time_elapsed="1:12"),
        ]
        self.assertEqual(detect_playoff_ot_winner(events, game), (9, 1))
        self.assertEqual(
            detect_playoff_ot_winner(events, SimpleNamespace(game_type="regular season", went_to_overtime=True)),
            (None, None),
        )

    def test_streaks_and_cutoffs(self) -> None:
        self.assertEqual(max_win_streak(["W", "W", "W", "L", "W", "W", "W", "W", "W"]), 5)
        self.assertEqual(max_win_streak(["L", "T", "L"]), 0)
        self.assertEqual(
            export_streak_len(
                [date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 10), date(2026, 1, 20)]
            ),
            3,
        )
        self.assertEqual(export_streak_len([date(2026, 1, 1), date(2026, 1, 20)]), 1)
        self.assertEqual(playoff_spot_cutoff(32), 16)
        self.assertEqual(playoff_spot_cutoff(16), 8)
        self.assertTrue(is_calder_award("CALDER TROPHY"))
        self.assertFalse(is_calder_award("HART TROPHY"))
        self.assertTrue(month_undefeated(["W", "W", "T", "W"]))
        self.assertFalse(month_undefeated(["W", "W", "L", "W"]))
        self.assertFalse(month_undefeated(["W", "W"]))
        self.assertEqual(major_award_slot("HART TROPHY"), "hart")
        self.assertIsNone(major_award_slot("JACK ADAMS TROPHY"))
        self.assertIsNone(major_award_slot("LADY BYNG TROPHY"))
        self.assertTrue(
            detect_consecutive_playoff_shutouts([(1, 9, 0), (2, 9, 0), (3, 9, 1)])
        )
        self.assertFalse(detect_consecutive_playoff_shutouts([(1, 9, 0), (2, 9, 1), (3, 9, 0)]))

    def test_road_win_after_dropping_first_two(self) -> None:
        games = [
            SimpleNamespace(home_team_id=10, away_team_id=20, home_score=3, away_score=1),
            SimpleNamespace(home_team_id=20, away_team_id=10, home_score=1, away_score=4),
            SimpleNamespace(home_team_id=10, away_team_id=20, home_score=2, away_score=3),
        ]
        self.assertTrue(detect_road_win_after_dropping_first_two(games, 20))
        self.assertFalse(detect_road_win_after_dropping_first_two(games, 10))

    def test_storage_keys_and_legacy_pairs(self) -> None:
        spec = CATALOG_BY_KEY["on_a_heater"]
        self.assertEqual(storage_key_for(spec, "2026-27"), "on_a_heater:2026-27")
        self.assertEqual(catalog_key_from_storage("on_a_heater:2026-27"), "on_a_heater")
        bender = CATALOG_BY_KEY["the_bender"]
        self.assertEqual(storage_key_for(bender, "2026-27", "2026-01"), "the_bender:2026-01")
        expanded = expand_legacy_pairs({(8, "on_a_heater")}, "2026-27")
        self.assertIn((8, "on_a_heater:2026-27"), expanded)
        rewritten = rewrite_truths_to_storage({8: {"on_a_heater": {"detail": "hot"}}}, "2026-27")
        self.assertIn("on_a_heater:2026-27", rewritten[8])
        already_month = rewrite_truths_to_storage(
            {8: {"the_bender:2026-01": {"period": "2026-01"}}}, "2026-27"
        )
        self.assertIn("the_bender:2026-01", already_month[8])


class CatalogTests(unittest.TestCase):
    def test_going_up_is_relegation_only(self) -> None:
        cap_keys = {item.key for item in catalog_for_league("bowl-cap")}
        hist_keys = {item.key for item in catalog_for_league("bowl-historical")}
        rel_keys = {item.key for item in catalog_for_league("bowl-fantasy")}
        self.assertNotIn("going_up", cap_keys)
        self.assertNotIn("going_up", hist_keys)
        self.assertIn("going_up", rel_keys)
        self.assertIn("pinnacle", cap_keys)
        first_wave = {
            "comeback_kids",
            "four_goal_night",
            "fight_night",
            "playoff_ot_hero",
            "on_a_heater",
            "home_cooking",
            "overtime_merchant",
            "three_star_season",
            "bargain_bin",
            "homegrown_core",
            "draft_steal",
            "calder_club",
            "nemesis",
            "statement_win",
            "reverse_sweep",
            "export_streak",
            "league_first_hat",
        }
        phase_two = {
            "league_first_shutout",
            "league_first_four",
            "special_teams_season",
            "the_bender",
            "elc_lightning",
            "kid_line_energy",
            "perfect_attendance",
            "guarantee_remixed",
            "swept_not_forgotten",
            "playoff_shutout_pair",
            "award_shelf",
            "homegrown_cup",
            "iron_decade",
        }
        phase_three = {"the_heist"}
        self.assertTrue(first_wave.issubset(cap_keys))
        self.assertTrue(phase_two.issubset(cap_keys))
        self.assertTrue(phase_three.issubset(cap_keys))
        self.assertTrue(first_wave.issubset(hist_keys))
        self.assertTrue(first_wave.issubset(rel_keys))
        self.assertNotIn("jack_adams", cap_keys)
        self.assertNotIn("captain_night", cap_keys)
        self.assertNotIn("captains_night", cap_keys)
        self.assertNotIn("Jack Adams", CATALOG_BY_KEY["award_shelf"].description)
        self.assertTrue(CATALOG_BY_KEY["reverse_sweep"].hidden)
        self.assertTrue(CATALOG_BY_KEY["award_shelf"].hidden)
        self.assertFalse(CATALOG_BY_KEY["comeback_kids"].hidden)
        self.assertTrue(CATALOG_BY_KEY["on_a_heater"].repeatable)
        self.assertTrue(CATALOG_BY_KEY["the_bender"].repeatable)
        self.assertEqual(CATALOG_BY_KEY["the_bender"].repeat_scope, "month")
        self.assertTrue(CATALOG_BY_KEY["league_first_hat"].race)
        self.assertTrue(CATALOG_BY_KEY["the_heist"].repeatable)

    def test_heist_and_export_recap_helpers(self) -> None:
        self.assertEqual(player_ids_from_drag_keys(["player:12", "pick:2027:1", "player:9"]), [12, 9])
        acquired = acquired_by_team_from_ledger(10, 20, ["player:5"], ["player:8", "pick:2028:2"])
        self.assertEqual(acquired[20], {5})
        self.assertEqual(acquired[10], {8})
        hits = detect_heist({20: {5}}, [(20, 5, 24), (20, 5, 30), (10, 8, 11)])
        self.assertEqual(hits, [(20, 5, 24)])
        self.assertEqual(detect_heist({20: {5}}, [(20, 9, 40)]), [])
        self.assertEqual(place_label(1), "1st")
        self.assertEqual(place_label(2), "2nd")
        self.assertEqual(place_label(4), "4th")
        title, body = format_export_recap(titles=["The Heist"], rank=4)
        self.assertEqual(title, "Export recap")
        self.assertEqual(
            body,
            "You unlocked The Heist. Scratch the ticket on Achievements to claim your AP. You're #4 on the trophy board.",
        )
        self.assertEqual(roll_reward_cell(_SeqRng([0.0])), 1)
        self.assertEqual(roll_reward_cell(_SeqRng([0.60])), 2)
        self.assertEqual(roll_reward_cell(_SeqRng([0.90])), 3)
        self.assertEqual(roll_reward_cells(_SeqRng([0.0, 0.60, 0.90])), [1, 2, 3])
        self.assertEqual(parse_reward_cells("[1, 3, 3]"), [1, 3, 3])
        self.assertIsNone(parse_reward_cells("[1, 4, 2]"))

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
        self.assertIn(ACHIEVEMENT_LEAGUE_FIRST_EVENT_KEY, DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[ACHIEVEMENT_UNLOCKED_EVENT_KEY], "achievements")
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[ACHIEVEMENT_LEAGUE_FIRST_EVENT_KEY], "achievements")

    def test_redeem_celebration_assets_exist(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        sheet = root / "app" / "static" / "img" / "fw-sheet.png"
        self.assertTrue(sheet.is_file(), "fw-sheet.png")
        self.assertGreater(sheet.stat().st_size, 1000)
        for name in ("crackle.wav", "fireworks.wav", "confetti.wav", "cheer.wav", "cheer-big.wav", "explosion.wav"):
            path = root / "app" / "static" / "sfx" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 1000, name)

    def test_scratch_preview_is_public(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["TESTING"] = True
        with app.test_client() as client:
            page = client.get("/achievements/scratch-preview")
            self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
            html = page.get_data(as_text=True)
            self.assertIn('data-preview="1"', html)
            self.assertIn("The Pinnacle", html)
            self.assertIn("gm-achievements__card--claimable", html)
            self.assertIn("AP ledger", html)


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
                source_ref = unlock_source_ref("bowl-cap", tid, "gordie_howe")
                unlock = db.session.scalar(
                    select(GmAchievementUnlock).where(GmAchievementUnlock.source_ref == source_ref).limit(1)
                )
                self.assertIsNotNone(unlock)
                self.assertIsNone(unlock.claimed_at)
                self.assertEqual(int(unlock.ap_delta or 0), 0)
                ledger = db.session.scalar(
                    select(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref).limit(1)
                )
                self.assertIsNone(ledger)
                self.assertEqual(unclaimed_unlock_count(db.session, "bowl-cap", tid), 1)
                self.assertEqual(sync_achievement_ap_ledger(db.session, "bowl-cap"), 0)
                started = start_achievement_scratch(
                    db.session,
                    league_slug="bowl-cap",
                    team_id=tid,
                    storage_key="gordie_howe",
                    rng=_SeqRng([0.0, 0.90, 0.90]),
                )
                self.assertTrue(started["ok"])
                self.assertEqual(started["cells"], [1, 3, 3])
                self.assertEqual(started["ticket_ap"], 7)
                again = start_achievement_scratch(
                    db.session,
                    league_slug="bowl-cap",
                    team_id=tid,
                    storage_key="gordie_howe",
                    rng=_SeqRng([0.0, 0.0, 0.0]),
                )
                self.assertEqual(again["cells"], [1, 3, 3])
                claimed = claim_achievement_scratch(
                    db.session,
                    league_slug="bowl-cap",
                    team_id=tid,
                    storage_key="gordie_howe",
                    created_by_user_id=None,
                )
                self.assertTrue(claimed["ok"])
                spec = CATALOG_BY_KEY["gordie_howe"]
                self.assertEqual(claimed["total_ap"], 7 * int(spec.ap))
                ledger = db.session.scalar(
                    select(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref).limit(1)
                )
                self.assertIsNotNone(ledger)
                self.assertEqual(int(ledger.delta), 7 * int(spec.ap))
                self.assertIn("Gordie Howe", str(ledger.meta_json or ""))
                self.assertEqual(unclaimed_unlock_count(db.session, "bowl-cap", tid), 0)
                twice = claim_achievement_scratch(
                    db.session,
                    league_slug="bowl-cap",
                    team_id=tid,
                    storage_key="gordie_howe",
                    created_by_user_id=None,
                )
                self.assertEqual(twice["total_ap"], 7 * int(spec.ap))
                third = evaluate_gm_achievements_after_import(self.app)
                self.assertEqual(third["awarded"], 0)

            db.session.rollback()

    def test_sync_writes_missing_achievement_ledger_row(self) -> None:
        self.app = create_app(make_league_config("bowl-cap"))
        with self.app.app_context():
            team = db.session.scalar(select(Team).order_by(Team.id).limit(1))
            self.assertIsNotNone(team)
            tid = int(team.id)
            spec = CATALOG_BY_KEY["gordie_howe"]
            source_ref = "gm_ach:bowl-cap:test-sync-ledger"
            unlock = GmAchievementUnlock(
                league_slug="bowl-cap",
                team_id=tid,
                user_id=None,
                achievement_key=spec.key,
                source_ref=source_ref,
                season_label="2026-27",
                meta_json="{}",
                ap_delta=int(spec.ap),
            )
            db.session.add(unlock)
            db.session.flush()
            pending = GmAchievementUnlock(
                league_slug="bowl-cap",
                team_id=tid,
                user_id=None,
                achievement_key="pinnacle",
                source_ref="gm_ach:bowl-cap:test-sync-unclaimed",
                season_label="2026-27",
                meta_json="{}",
                ap_delta=0,
            )
            db.session.add(pending)
            db.session.flush()
            created = sync_achievement_ap_ledger(db.session, "bowl-cap")
            self.assertGreaterEqual(created, 1)
            self.assertIsNotNone(unlock.claimed_at)
            self.assertIsNone(pending.claimed_at)
            self.assertIsNone(
                db.session.scalar(
                    select(ApLedgerEntry).where(
                        ApLedgerEntry.source_ref == "gm_ach:bowl-cap:test-sync-unclaimed"
                    ).limit(1)
                )
            )
            row = db.session.scalar(
                select(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref).limit(1)
            )
            self.assertIsNotNone(row)
            self.assertEqual(int(row.delta), int(spec.ap))
            self.assertEqual(credit_achievement_ap(
                league_slug="bowl-cap",
                team_id=tid,
                spec=spec,
                source_ref=source_ref,
                created_by_user_id=None,
            ), None)
            db.session.rollback()

    def test_scratch_routes_require_gm_and_reject_double_claim(self) -> None:
        self.app = create_app(make_league_config("bowl-cap"))
        self.app.config["WTF_CSRF_ENABLED"] = False
        with self.app.app_context():
            team = db.session.scalar(select(Team).order_by(Team.id).limit(1))
            self.assertIsNotNone(team)
            tid = int(team.id)
            user = User(
                email="gm-ach-scratch-route@example.invalid",
                password_hash="x",
                discord_name="Scratch Route",
            )
            db.session.add(user)
            db.session.flush()
            uid = int(user.id)
            db.session.add(
                GmLeagueMembership(
                    league_slug="bowl-cap",
                    user_id=uid,
                    team_id=tid,
                    status="active",
                )
            )
            spec = CATALOG_BY_KEY["gordie_howe"]
            store_key = "gordie_howe:test-scratch-route"
            source_ref = "gm_ach:bowl-cap:test-scratch-route"
            db.session.add(
                GmAchievementUnlock(
                    league_slug="bowl-cap",
                    team_id=tid,
                    user_id=uid,
                    achievement_key=store_key,
                    source_ref=source_ref,
                    season_label="2026-27",
                    meta_json="{}",
                    ap_delta=0,
                )
            )
            db.session.commit()
            try:
                with self.app.test_client() as guest:
                    denied = guest.post("/achievements/scratch/start", json={"storage_key": store_key})
                    self.assertIn(denied.status_code, (302, 401, 403))
                with self.app.test_client() as client:
                    fake_gm = SimpleNamespace(
                        is_authenticated=True,
                        is_active=True,
                        is_anonymous=False,
                        is_admin=False,
                        id=uid,
                        get_id=lambda: str(uid),
                    )
                    with patch("flask_login.utils._get_user", return_value=fake_gm):
                        start = client.post("/achievements/scratch/start", json={"storage_key": store_key})
                        self.assertEqual(start.status_code, 200, start.get_data(as_text=True))
                        body = start.get_json()
                        self.assertTrue(body["ok"])
                        self.assertEqual(len(body["cells"]), 3)
                        claim = client.post("/achievements/scratch/claim", json={"storage_key": store_key})
                        self.assertEqual(claim.status_code, 200, claim.get_data(as_text=True))
                        first = claim.get_json()
                        self.assertEqual(first["total_ap"], first["ticket_ap"] * first["multiplier"])
                        again = client.post("/achievements/scratch/claim", json={"storage_key": store_key})
                        self.assertEqual(again.status_code, 200)
                        self.assertEqual(again.get_json()["total_ap"], first["total_ap"])
                    rows = list(
                        db.session.scalars(
                            select(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref)
                        ).all()
                    )
                    self.assertEqual(len(rows), 1)
            finally:
                db.session.execute(delete(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref))
                db.session.execute(
                    delete(GmAchievementUnlock).where(GmAchievementUnlock.source_ref == source_ref)
                )
                db.session.execute(delete(GmLeagueMembership).where(GmLeagueMembership.user_id == uid))
                db.session.execute(delete(User).where(User.id == uid))
                db.session.commit()

    def test_achievements_page_glow_scratch_and_ap_balance(self) -> None:
        from app.services.ap_service import team_ap_balance

        self.app = create_app(make_league_config("bowl-cap"))
        self.app.config["WTF_CSRF_ENABLED"] = False
        with self.app.app_context():
            team = db.session.scalar(select(Team).order_by(Team.id).limit(1))
            self.assertIsNotNone(team)
            tid = int(team.id)
            user = User(
                email="gm-ach-page-scratch@example.invalid",
                password_hash="x",
                discord_name="Page Scratch",
            )
            db.session.add(user)
            db.session.flush()
            uid = int(user.id)
            db.session.add(
                GmLeagueMembership(
                    league_slug="bowl-cap",
                    user_id=uid,
                    team_id=tid,
                    status="active",
                )
            )
            store_key = "gordie_howe"
            source_ref = "gm_ach:bowl-cap:test-page-scratch"
            db.session.add(
                GmAchievementUnlock(
                    league_slug="bowl-cap",
                    team_id=tid,
                    user_id=uid,
                    achievement_key=store_key,
                    source_ref=source_ref,
                    season_label="2026-27",
                    meta_json="{}",
                    ap_delta=0,
                )
            )
            db.session.commit()
            before = int(team_ap_balance("bowl-cap", tid))
            try:
                with self.app.test_client() as client:
                    fake_gm = SimpleNamespace(
                        is_authenticated=True,
                        is_active=True,
                        is_anonymous=False,
                        is_admin=False,
                        id=uid,
                        email="gm-ach-page-scratch@example.invalid",
                        username=None,
                        get_id=lambda: str(uid),
                    )
                    with patch("flask_login.utils._get_user", return_value=fake_gm):
                        page = client.get("/achievements")
                        self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
                        html = page.get_data(as_text=True)
                        self.assertIn("header-tools__link--ach-ready", html)
                        self.assertIn("gm-achievements__card--claimable", html)
                        self.assertIn(f'data-storage-key="{store_key}"', html)
                        self.assertGreaterEqual(html.count("gm-ach-scratch__cell"), 3)
                        self.assertIn("Ticket ready", html)
                        self.assertIn("Scratch", html)
                        self.assertRegex(html, r"×[1-5]")
                        start = client.post("/achievements/scratch/start", json={"storage_key": store_key})
                        self.assertEqual(start.status_code, 200, start.get_data(as_text=True))
                        started = start.get_json()
                        self.assertEqual(len(started["cells"]), 3)
                        claim = client.post("/achievements/scratch/claim", json={"storage_key": store_key})
                        self.assertEqual(claim.status_code, 200, claim.get_data(as_text=True))
                        paid = claim.get_json()
                        total = int(paid["total_ap"])
                        self.assertEqual(total, int(paid["ticket_ap"]) * int(paid["multiplier"]))
                        self.assertEqual(int(paid["balance"]), before + total)
                        ap_page = client.get("/action-points")
                        self.assertEqual(ap_page.status_code, 200, ap_page.get_data(as_text=True))
                        ap_html = ap_page.get_data(as_text=True)
                        self.assertIn(f"{paid['balance']} AP", ap_html)
                        after = client.get("/achievements")
                        self.assertEqual(after.status_code, 200)
                        after_html = after.get_data(as_text=True)
                        self.assertNotIn(f'data-storage-key="{store_key}"', after_html)
                        self.assertIn(
                            f"{started['cells'][0]} + {started['cells'][1]} + {started['cells'][2]}",
                            after_html,
                        )
                        self.assertIn(f"= {total} AP", after_html)
                        if int(paid.get("unclaimed") or 0) == 0:
                            self.assertNotIn("header-tools__link--ach-ready", after_html)
            finally:
                db.session.execute(delete(ApLedgerEntry).where(ApLedgerEntry.source_ref == source_ref))
                db.session.execute(
                    delete(GmAchievementUnlock).where(GmAchievementUnlock.source_ref == source_ref)
                )
                db.session.execute(delete(GmLeagueMembership).where(GmLeagueMembership.user_id == uid))
                db.session.execute(delete(User).where(User.id == uid))
                db.session.commit()

    def test_page_payload_hides_reverse_sweep_and_discover_runs(self) -> None:
        self.app = create_app(make_league_config("bowl-cap"))
        with self.app.app_context():
            payload = build_achievements_page_payload(
                db.session, league_slug="bowl-cap", membership=None
            )
            cards = {card["key"]: card for card in payload["items"]}
            self.assertIn("comeback_kids", cards)
            self.assertIn("the_bender", cards)
            self.assertNotIn("jack_adams", cards)
            self.assertNotIn("captain_night", cards)
            self.assertIn("the_heist", cards)
            hidden = cards["reverse_sweep"]
            self.assertEqual(hidden["title"], "???")
            self.assertTrue(hidden["hidden"])
            self.assertIn("Hidden achievement", hidden["description"])
            self.assertTrue(cards["award_shelf"]["hidden"])
            with self.app.test_request_context("/achievements"):
                html = render_template("gm_achievements.html", membership=None, **payload)
            self.assertIn("Comeback Kids", html)
            self.assertIn("The Bender", html)
            self.assertIn("The Heist", html)
            self.assertIn("gm-ach-scratch-dialog", html)
            self.assertIn("id=\"gm-ach-fw\"", html)
            self.assertIn("data-ach-sfx=\"fireworks\"", html)
            self.assertIn("img/fw-sheet.png", html)
            self.assertIn("Got it", html)
            self.assertIn("×1", html)
            self.assertNotRegex(html, r"\+\d+ AP")
            self.assertIn("Hidden achievement", html)
            self.assertIn("Export Streak", html)
            self.assertIn("???", html)
            self.assertNotIn("Jack Adams", html)
            self.assertNotIn("Captain's Night", html)
            truths = discover_true_achievements(db.session, "bowl-cap")
            self.assertIsInstance(truths, dict)
            board = build_achievement_leaderboard(db.session, "bowl-cap")
            self.assertIn("rows", board)
            self.assertIn("firsts", board)
            with self.app.test_request_context("/achievement-leaders"):
                leaders_html = render_template("gm_achievement_leaders.html", **board)
            self.assertIn("GM Trophy Leaderboard", leaders_html)
            sample = dict(board)
            sample["firsts"] = [
                {
                    "title": "Comeback Kids",
                    "team_name": "Toronto",
                    "team_slug": "",
                    "gm_name": "Test GM",
                    "unlocked_at": None,
                }
            ]
            with self.app.test_request_context("/achievement-leaders"):
                firsts_html = render_template("gm_achievement_leaders.html", **sample)
            self.assertIn("Who unlocked this first", firsts_html)
            self.assertIn("Comeback Kids", firsts_html)
            team = db.session.scalars(select(Team).limit(1)).first()
            if team is not None:
                rival = build_achievement_rival_page(db.session, "bowl-cap", int(team.id))
                self.assertIsNotNone(rival)
                with self.app.test_request_context(f"/achievement-leaders/{team.slug}"):
                    rival_html = render_template("gm_achievement_rival.html", **rival)
                self.assertIn(team.full_display_name(), rival_html)
                self.assertIn("Who unlocked this first", rival_html)


if __name__ == "__main__":
    unittest.main()
