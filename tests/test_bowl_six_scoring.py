"""BOWL Six scoring and validation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.bowl_six_scoring import (
    position_kind,
    score_skater_line,
    slot_accepts_position,
)
from datetime import date, datetime

from app.services.bowl_six import (
    default_lock_at,
    eastern_naive_from_utc_naive,
    ensure_bowl_six_slate_prize_ledgers,
    ensure_current_slate_after_finalization,
    get_or_create_current_slate,
    lock_at_display_eastern,
    lock_at_iso_z,
    maybe_enqueue_bowl_six_roster_reminders,
    parse_lock_at_eastern_form,
    slate_award_at,
    slate_real_scoring_window_utc,
    slate_lock_ui,
    slate_week_rs_games_complete,
    sync_bowl_six_slate_ap_awards,
    sync_slate_week_to_league_calendar,
    utc_naive_from_eastern,
    validate_lineup_picks,
)
from app.site_models import BowlSixSlate


class BowlSixScoringTest(unittest.TestCase):
    def test_position_kind(self):
        self.assertEqual(position_kind("G"), "gk")
        self.assertEqual(position_kind("LD"), "def")
        self.assertEqual(position_kind("C"), "fwd")

    def test_slot_accepts(self):
        self.assertTrue(slot_accepts_position("gk", "G"))
        self.assertFalse(slot_accepts_position("gk", "C"))
        self.assertTrue(slot_accepts_position("def1", "D"))

    def test_discipline_reduces_positive_points(self):
        line = MagicMock()
        line.goals = 1
        line.assists = 0
        line.shots = 0
        line.plus_minus = 0
        line.hits = 0
        line.blocked_shots = 0
        line.pim = 0
        hi, br = score_skater_line(line, discipline=0.7, gwg=False)
        lo, _ = score_skater_line(line, discipline=1.0, gwg=False)
        self.assertGreater(lo, hi)
        self.assertAlmostEqual(lo, 6.0)
        self.assertAlmostEqual(hi, 4.2)

    def test_locked_slate_rejects_save(self):
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=__import__("datetime").date(2026, 1, 5),
            week_end=__import__("datetime").date(2026, 1, 11),
            lock_at=__import__("datetime").datetime(2020, 1, 1),
            status="locked",
        )
        session = MagicMock()
        league_session = MagicMock()
        v = validate_lineup_picks(
            session,
            league_session,
            league_slug="bowl-cap",
            slate=slate,
            user_id=1,
            picks={s: 1 for s in ("gk", "def1", "def2", "fwd1", "fwd2", "fwd3")},
            captain_player_id=2,
        )
        self.assertFalse(v.ok)

    def test_slate_week_complete_uses_real_time_window(self):
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=__import__("datetime").datetime(2026, 5, 18),
            status="locked",
        )
        league_session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six.utcnow_naive",
            return_value=__import__("datetime").datetime(2026, 5, 25, 3, 59),
        ):
            self.assertFalse(slate_week_rs_games_complete(league_session, slate))
        with unittest.mock.patch(
            "app.services.bowl_six.utcnow_naive",
            return_value=__import__("datetime").datetime(2026, 5, 25, 4, 0),
        ):
            self.assertTrue(slate_week_rs_games_complete(league_session, slate))

    def test_parse_lock_at_eastern_form(self):
        # May 19 2026 8:30 PM EDT (UTC-4) -> May 20 00:30 UTC
        self.assertEqual(
            parse_lock_at_eastern_form("2026-05-19", "20:30"),
            __import__("datetime").datetime(2026, 5, 20, 0, 30),
        )
        self.assertIsNone(parse_lock_at_eastern_form("", "12:00"))

    def test_default_bowl_six_lock_is_monday_759_pm_et(self):
        session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six.get_rule_value", return_value="19:59"
        ):
            self.assertEqual(
                default_lock_at(date(2026, 5, 18), "bowl-cap", session),
                __import__("datetime").datetime(2026, 5, 18, 23, 59),
            )

    def test_default_bowl_six_lock_normalizes_legacy_8pm_et(self):
        session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six.get_rule_value", return_value="20:00"
        ):
            self.assertEqual(
                default_lock_at(date(2026, 5, 18), "bowl-historical", session),
                __import__("datetime").datetime(2026, 5, 18, 23, 59),
            )

    def test_slate_award_at_is_next_monday_midnight_et(self):
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=__import__("datetime").datetime(2026, 5, 18, 23, 59),
            status="locked",
        )
        self.assertEqual(
            slate_award_at(slate),
            __import__("datetime").datetime(2026, 5, 25, 4, 0),
        )

    def test_scoring_window_starts_immediately_after_759_lock_for_all_leagues(self):
        for slug in ("bowl-fantasy", "bowl-cap", "bowl-historical"):
            slate = BowlSixSlate(
                league_slug=slug,
                week_start=date(2026, 5, 18),
                week_end=date(2026, 5, 24),
                lock_at=__import__("datetime").datetime(2026, 5, 18, 23, 59),
                status="locked",
            )
            start, end = slate_real_scoring_window_utc(slate)
            self.assertEqual(start, __import__("datetime").datetime(2026, 5, 19, 0, 0))
            self.assertEqual(end, __import__("datetime").datetime(2026, 5, 25, 4, 0))

    def test_eastern_utc_round_trip(self):
        dt = __import__("datetime").datetime(2026, 5, 20, 0, 30)
        et = eastern_naive_from_utc_naive(dt)
        self.assertEqual(et, __import__("datetime").datetime(2026, 5, 19, 20, 30))
        self.assertEqual(utc_naive_from_eastern(et), dt)

    def test_lock_at_display_eastern(self):
        dt = __import__("datetime").datetime(2026, 5, 20, 0, 30)
        text = lock_at_display_eastern(dt)
        self.assertIn("May 19, 2026", text)
        self.assertIn("8:30 PM", text)
        self.assertIn("ET", text)

    def test_lock_at_iso_z(self):
        dt = __import__("datetime").datetime(2026, 5, 19, 20, 0)
        self.assertEqual(lock_at_iso_z(dt), "2026-05-19T20:00:00Z")

    def test_slate_lock_ui_countdown_when_open(self):
        future = __import__("datetime").datetime(2099, 1, 1, 12, 0)
        slate = BowlSixSlate(
            league_slug="bowl",
            week_start=date(2098, 12, 25),
            week_end=date(2098, 12, 31),
            status="open",
            lock_at=future,
        )
        ui = slate_lock_ui(slate)
        self.assertTrue(ui["show_countdown"])
        self.assertEqual(ui["banner_label"], "Lineup locks in")

    def test_slate_lock_ui_countdown_when_locked_but_future(self):
        future = __import__("datetime").datetime(2099, 1, 1, 12, 0)
        slate = BowlSixSlate(
            league_slug="bowl",
            week_start=date(2098, 12, 25),
            week_end=date(2098, 12, 31),
            status="locked",
            lock_at=future,
        )
        ui = slate_lock_ui(slate)
        self.assertTrue(ui["show_countdown"])

    def test_sync_reopens_when_lock_extended(self):
        future = __import__("datetime").datetime(2099, 1, 1, 12, 0)
        slate = BowlSixSlate(
            league_slug="bowl",
            week_start=date(2098, 12, 25),
            week_end=date(2098, 12, 31),
            status="locked",
            lock_at=future,
        )
        from app.services.bowl_six import sync_slate_lock_status

        sync_slate_lock_status(unittest.mock.MagicMock(), slate)
        self.assertEqual(slate.status, "open")

    def test_sync_slate_week_preserves_prior_week_lineups_for_repeat_blocking(self):
        slate = BowlSixSlate(
            league_slug="bowl-historical",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            scoring_week_start=None,
            scoring_week_end=None,
            lock_at=__import__("datetime").datetime(2026, 5, 18),
            status="open",
        )
        site_session = MagicMock()
        site_session.scalar.return_value = None
        league_session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six._real_bowl_six_week_bounds",
            return_value=(date(2026, 5, 25), date(2026, 5, 31)),
        ):
            changed = sync_slate_week_to_league_calendar(
                site_session, league_session, "bowl-historical", slate
            )
        self.assertTrue(changed)
        self.assertEqual(slate.week_start, date(2026, 5, 18))
        self.assertEqual(slate.week_end, date(2026, 5, 24))
        self.assertEqual(slate.scoring_week_start, date(2026, 5, 18))
        self.assertEqual(slate.scoring_week_end, date(2026, 5, 24))

    def test_current_slate_creation_does_not_query_latest_open_prior_slate(self):
        class FakeSession:
            def __init__(self):
                self.added = None

            def scalar(self, stmt):
                sql = str(stmt)
                if "status IN" in sql:
                    raise AssertionError("current slate lookup must not reuse an older open slate")
                return None

            def add(self, obj):
                self.added = obj

            def flush(self):
                pass

        site_session = FakeSession()
        with unittest.mock.patch("app.services.bowl_six.bowl_six_enabled", return_value=True), \
            unittest.mock.patch(
                "app.services.bowl_six._real_bowl_six_week_bounds",
                return_value=(date(2026, 5, 25), date(2026, 5, 31)),
            ), \
            unittest.mock.patch(
                "app.services.bowl_six._current_scoring_week_bounds",
                return_value=(date(2026, 5, 25), date(2026, 5, 31)),
            ), \
            unittest.mock.patch(
                "app.services.bowl_six.default_lock_at",
                return_value=__import__("datetime").datetime(2099, 1, 1, 0, 0),
            ):
            slate = get_or_create_current_slate(
                site_session, "bowl-historical", league_session=MagicMock()
            )
        self.assertIs(slate, site_session.added)
        self.assertEqual(slate.week_start, date(2026, 5, 25))

    def test_finalize_reset_opens_current_week_only_after_calendar_advances(self):
        prior_slate = BowlSixSlate(
            league_slug="bowl-historical",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=__import__("datetime").datetime(2026, 5, 18, 23, 59),
            status="scored",
        )

        class FakeSession:
            def __init__(self):
                self.added = None

            def scalar(self, _stmt):
                return None

            def add(self, obj):
                self.added = obj

            def flush(self):
                pass

        site_session = FakeSession()
        with unittest.mock.patch("app.services.bowl_six.bowl_six_enabled", return_value=True), \
            unittest.mock.patch(
                "app.services.bowl_six._real_bowl_six_week_bounds",
                return_value=(date(2026, 5, 25), date(2026, 5, 31)),
            ), \
            unittest.mock.patch(
                "app.services.bowl_six._current_scoring_week_bounds",
                return_value=(date(2026, 5, 25), date(2026, 5, 31)),
            ), \
            unittest.mock.patch(
                "app.services.bowl_six.default_lock_at",
                return_value=__import__("datetime").datetime(2099, 1, 1, 0, 0),
            ):
            current = ensure_current_slate_after_finalization(
                site_session, MagicMock(), prior_slate
            )
        self.assertIs(current, site_session.added)
        self.assertEqual(current.week_start, date(2026, 5, 25))
        self.assertEqual(current.status, "open")

    def test_slate_lock_ui_locked_when_past(self):
        past = __import__("datetime").datetime(2020, 1, 1, 0, 0)
        slate = BowlSixSlate(
            league_slug="bowl",
            week_start=date(2019, 12, 25),
            week_end=date(2019, 12, 31),
            status="open",
            lock_at=past,
        )
        ui = slate_lock_ui(slate)
        self.assertFalse(ui["show_countdown"])
        self.assertEqual(ui["banner_label"], "Lineups locked")

    def test_bowl_six_ap_awards_are_versioned_for_rescore_reaward(self):
        slate = BowlSixSlate(
            id=99,
            league_slug="bowl-historical",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=datetime(2026, 5, 18, 23, 59),
            status="scored",
            scoring_version=3,
            ap_place1_team_id=20,
        )
        session = MagicMock()
        mem = MagicMock(team_id=10)
        session.scalar.return_value = mem
        with unittest.mock.patch(
            "app.services.bowl_six.slate_rankings",
            return_value=[{"user_id": 1, "total_points": 100.0}],
        ), unittest.mock.patch("app.services.bowl_six.add_ledger_entry") as add_entry:
            sync_bowl_six_slate_ap_awards(session, slate)
        source_refs = [call.kwargs["source_ref"] for call in add_entry.call_args_list]
        self.assertIn("bowl_six:slate:99:place:1:rev:3", source_refs)
        self.assertIn("bowl_six:slate:99:place:1:award:3", source_refs)
        self.assertEqual(slate.ap_place1_team_id, 10)

    def test_bowl_six_prize_ledger_repair_adds_missing_award(self):
        slate = BowlSixSlate(
            id=99,
            league_slug="bowl-historical",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=datetime(2026, 5, 18, 23, 59),
            status="scored",
            scoring_version=3,
            ap_place1_team_id=10,
        )
        with unittest.mock.patch("app.services.bowl_six.sync_bowl_six_slate_ap_awards"), \
            unittest.mock.patch(
                "app.services.bowl_six.slate_rankings",
                return_value=[{"user_id": 1, "total_points": 100.0}],
            ), \
            unittest.mock.patch(
                "app.services.bowl_six._bowl_six_award_ledger_exists",
                return_value=False,
            ), \
            unittest.mock.patch("app.services.bowl_six.add_ledger_entry", return_value=object()) as add_entry:
            created = ensure_bowl_six_slate_prize_ledgers(MagicMock(), slate)
        self.assertEqual(created, 1)
        self.assertEqual(add_entry.call_args.kwargs["team_id"], 10)
        self.assertEqual(add_entry.call_args.kwargs["delta"], 10)
        self.assertTrue(add_entry.call_args.kwargs["meta"]["repair"])

    def test_bowl_six_roster_reminders_queue_unlock_and_warning(self):
        slate = BowlSixSlate(
            id=99,
            league_slug="bowl-historical",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=datetime(2026, 5, 18, 23, 59),
            status="open",
            label="Week of 2026-05-18",
        )
        session = MagicMock()
        with unittest.mock.patch("app.services.bowl_six.bowl_six_enabled", return_value=True), \
            unittest.mock.patch("app.services.bowl_six.get_or_create_current_slate", return_value=slate), \
            unittest.mock.patch("app.services.bowl_six._gm_role_mention_for_league", return_value="<@&123456789012345678>"), \
            unittest.mock.patch("app.services.discord_events.build_league_public_url", return_value="https://site/bowl-historical/bowl-six"), \
            unittest.mock.patch("app.services.discord_events.enqueue_discord_event", return_value=object()) as enqueue:
            queued = maybe_enqueue_bowl_six_roster_reminders(
                session,
                "bowl-historical",
                now=datetime(2026, 5, 18, 23, 30),
            )
        self.assertEqual(queued, 2)
        event_keys = [call.kwargs["event_key"] for call in enqueue.call_args_list]
        self.assertEqual(event_keys, ["bowl_six_rosters_unlocked", "bowl_six_lock_warning"])
        payload = enqueue.call_args_list[0].kwargs["payload"]
        self.assertIn("<@&123456789012345678>", payload["body"])

    def test_bowl_six_lock_warning_not_queued_after_lock(self):
        slate = BowlSixSlate(
            id=99,
            league_slug="bowl-cap",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=datetime(2026, 5, 18, 23, 59),
            status="locked",
        )
        session = MagicMock()
        with unittest.mock.patch("app.services.bowl_six.bowl_six_enabled", return_value=True), \
            unittest.mock.patch("app.services.bowl_six.get_or_create_current_slate", return_value=slate), \
            unittest.mock.patch("app.services.discord_events.build_league_public_url", return_value="https://site/bowl-cap/bowl-six"), \
            unittest.mock.patch("app.services.discord_events.enqueue_discord_event", return_value=object()) as enqueue:
            maybe_enqueue_bowl_six_roster_reminders(
                session,
                "bowl-cap",
                now=datetime(2026, 5, 19, 0, 0),
            )
        event_keys = [call.kwargs["event_key"] for call in enqueue.call_args_list]
        self.assertEqual(event_keys, ["bowl_six_rosters_unlocked"])


if __name__ == "__main__":
    unittest.main()
