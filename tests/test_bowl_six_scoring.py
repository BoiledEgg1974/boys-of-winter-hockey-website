"""BOWL Six scoring and validation."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.bowl_six_scoring import (
    position_kind,
    score_skater_line,
    slot_accepts_position,
)
from datetime import date, datetime

from app.services.bowl_six import (
    award_bowl_six_season_prizes,
    bowl_six_season_bounds_for_week,
    default_lock_at,
    bowl_six_real_season_bounds,
    eastern_naive_from_utc_naive,
    ensure_bowl_six_slate_prize_ledgers,
    ensure_current_slate_after_finalization,
    get_or_create_current_slate,
    gm_season_standings,
    lock_at_display_eastern,
    lock_at_iso_z,
    maybe_enqueue_bowl_six_roster_reminders,
    parse_lock_at_eastern_form,
    season_ap_award_at,
    slate_award_at,
    slate_real_scoring_window_utc,
    season_ap_prize_for_rank,
    slate_lock_ui,
    slate_week_rs_games_complete,
    sync_bowl_six_slate_ap_awards,
    sync_slate_week_to_league_calendar,
    unlock_slate_for_edits,
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

    def test_season_ap_prizes_by_rank(self):
        self.assertEqual(season_ap_prize_for_rank(1), 30)
        self.assertEqual(season_ap_prize_for_rank(2), 20)
        self.assertEqual(season_ap_prize_for_rank(3), 10)
        self.assertEqual(season_ap_prize_for_rank(4), 2)

    def test_bowl_six_real_season_bounds(self):
        self.assertEqual(
            bowl_six_real_season_bounds(date(2026, 6, 3)),
            (date(2025, 7, 1), date(2026, 6, 30)),
        )
        self.assertEqual(
            bowl_six_real_season_bounds(date(2026, 7, 1)),
            (date(2026, 7, 1), date(2027, 6, 30)),
        )

    def test_bowl_six_season_bounds_for_week(self):
        self.assertEqual(
            bowl_six_season_bounds_for_week(date(2026, 3, 10)),
            (date(2025, 7, 1), date(2026, 6, 30)),
        )
        self.assertEqual(
            bowl_six_season_bounds_for_week(date(2026, 8, 1)),
            (date(2026, 7, 1), date(2027, 6, 30)),
        )

    def test_season_ap_award_at_is_july_first_midnight_et(self):
        self.assertEqual(
            season_ap_award_at(date(2026, 6, 30)),
            datetime(2026, 7, 1, 4, 0),
        )

    def test_award_bowl_six_season_prizes_writes_ledger_rows(self):
        season_start = date(2025, 7, 1)
        season_end = date(2026, 6, 30)
        session = MagicMock()
        session.scalar.return_value = None
        standings = [
            {"rank": 1, "team_id": 10, "season_points": 100.0, "weeks_played": 5},
            {"rank": 2, "team_id": 20, "season_points": 80.0, "weeks_played": 5},
        ]
        with unittest.mock.patch(
            "app.services.bowl_six.season_ap_award_time_reached", return_value=True
        ), unittest.mock.patch(
            "app.services.bowl_six.gm_season_standings", return_value=standings
        ), unittest.mock.patch(
            "app.services.bowl_six.add_ledger_entry", return_value=object()
        ) as add_entry:
            created = award_bowl_six_season_prizes(
                session, "bowl-cap", season_start, season_end
            )
        self.assertEqual(created, 2)
        self.assertEqual(add_entry.call_args_list[0].kwargs["delta"], 30)
        self.assertEqual(add_entry.call_args_list[1].kwargs["delta"], 20)
        self.assertEqual(
            add_entry.call_args_list[0].kwargs["reason_code"], "bowl_six_season_prize"
        )

    def test_award_bowl_six_season_prizes_skips_existing_rows(self):
        season_start = date(2025, 7, 1)
        season_end = date(2026, 6, 30)
        session = MagicMock()
        session.scalar.return_value = 99
        standings = [{"rank": 1, "team_id": 10, "season_points": 100.0, "weeks_played": 5}]
        with unittest.mock.patch(
            "app.services.bowl_six.season_ap_award_time_reached", return_value=True
        ), unittest.mock.patch(
            "app.services.bowl_six.gm_season_standings", return_value=standings
        ), unittest.mock.patch("app.services.bowl_six.add_ledger_entry") as add_entry:
            created = award_bowl_six_season_prizes(
                session, "bowl-cap", season_start, season_end
            )
        self.assertEqual(created, 0)
        add_entry.assert_not_called()

    def test_gm_season_standings_uses_current_active_gms_by_team(self):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        active_current = SimpleNamespace(id=10, user_id=99, team_id=12, status="active")
        active_other = SimpleNamespace(id=11, user_id=100, team_id=22, status="active")
        inactive_prior = SimpleNamespace(id=9, user_id=28, team_id=12, status="inactive")
        session = MagicMock()
        session.scalars.side_effect = [
            _Result([active_current, active_other]),
            _Result([inactive_prior]),
        ]
        session.execute.return_value = _Result([(28, 1, 7.5)])

        rows = gm_season_standings(
            session,
            "bowl-cap",
            season_start=date(2026, 1, 1),
            season_end=date(2026, 12, 31),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_id"], 99)
        self.assertEqual(rows[0]["team_id"], 12)
        self.assertEqual(rows[0]["season_points"], 7.5)
        self.assertEqual(rows[0]["weeks_played"], 1)
        self.assertNotIn(28, {row["user_id"] for row in rows})
        self.assertEqual(rows[1]["user_id"], 100)
        self.assertEqual(rows[1]["season_points"], 0.0)

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

    def test_custom_scoring_dates_start_at_midnight_et(self):
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            scoring_week_start=date(2026, 6, 16),
            scoring_week_end=date(2026, 6, 20),
            lock_at=__import__("datetime").datetime(2026, 6, 15, 23, 59),
            status="locked",
        )

        start, end = slate_real_scoring_window_utc(slate)

        self.assertEqual(start, __import__("datetime").datetime(2026, 6, 16, 4, 0))
        self.assertEqual(end, __import__("datetime").datetime(2026, 6, 21, 4, 0))

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

    def test_unlock_slate_for_edits_extends_past_lock(self):
        now = datetime(2026, 6, 16, 10, 0)
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            status="locked",
            lock_at=datetime(2026, 6, 15, 23, 59),
        )

        extended = unlock_slate_for_edits(slate, now=now)

        self.assertTrue(extended)
        self.assertEqual(slate.status, "open")
        self.assertEqual(slate.lock_at, datetime(2026, 6, 16, 12, 0))

    def test_unlock_slate_for_edits_keeps_future_lock(self):
        now = datetime(2026, 6, 16, 10, 0)
        future = datetime(2026, 6, 16, 18, 0)
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 6, 15),
            week_end=date(2026, 6, 21),
            status="locked",
            lock_at=future,
        )

        extended = unlock_slate_for_edits(slate, now=now)

        self.assertFalse(extended)
        self.assertEqual(slate.status, "open")
        self.assertEqual(slate.lock_at, future)

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

    def test_sync_slate_week_final_markers_backfills_without_status_transition(self):
        from datetime import datetime as dt

        from app.models import Game
        from app.services.bowl_six import _sync_slate_week_final_markers
        from app.site_models import BowlSixSlate

        slate = BowlSixSlate(
            id=55,
            league_slug="bowl-historical",
            week_start=date(1969, 3, 10),
            week_end=date(1969, 3, 16),
            scoring_week_start=date(1969, 3, 10),
            scoring_week_end=date(1969, 3, 16),
            status="open",
            lock_at=dt(2099, 1, 1),
        )
        season = MagicMock(id=1)
        game = Game(
            id=901,
            season_id=1,
            home_team_id=1,
            away_team_id=2,
            game_date=date(1969, 3, 12),
            status="final",
        )
        site_session = MagicMock()
        league_session = MagicMock()
        league_session.scalars.return_value.unique.return_value.all.return_value = [game]
        league_session.scalars.return_value.all.return_value = [game]
        observed = dt(2026, 3, 12, 1, 0)
        with unittest.mock.patch(
            "app.services.bowl_six.get_current_season", return_value=season
        ), unittest.mock.patch(
            "app.services.bowl_six.record_bowl_six_game_finals", return_value=1
        ) as record_markers, unittest.mock.patch(
            "app.services.bowl_six._marker_observed_at_for_slate",
            return_value=observed,
        ):
            n = _sync_slate_week_final_markers(site_session, league_session, slate)
        self.assertEqual(n, 1)
        record_markers.assert_called_once_with(
            site_session,
            league_session,
            league_slug="bowl-historical",
            game_ids=[901],
            observed_at=observed,
        )

    def test_open_slate_auto_update_enqueues_discord_after_marker_sync(self):
        slate = BowlSixSlate(
            id=56,
            league_slug="bowl-historical",
            week_start=date(1969, 3, 10),
            week_end=date(1969, 3, 16),
            status="open",
            lock_at=__import__("datetime").datetime(2099, 1, 1),
        )
        site_session = MagicMock()
        league_session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six._backfill_active_slate_final_markers_from_legacy_window",
            return_value=0,
        ), unittest.mock.patch(
            "app.services.bowl_six._record_current_calendar_final_markers_for_active_slate",
            return_value=1,
        ), unittest.mock.patch(
            "app.services.bowl_six.sync_slate_week_to_league_calendar", return_value=False
        ), unittest.mock.patch(
            "app.services.bowl_six.sync_slate_lock_status"
        ), unittest.mock.patch(
            "app.services.bowl_six.rs_game_ids_for_slate", return_value=[901]
        ), unittest.mock.patch(
            "app.services.bowl_six.refresh_player_week_stats"
        ), unittest.mock.patch(
            "app.services.bowl_six.refresh_slate_lineup_scores", return_value=2
        ), unittest.mock.patch(
            "app.services.bowl_six.is_current_bowl_six_week",
            return_value=True,
        ), unittest.mock.patch(
            "app.services.bowl_six._enqueue_bowl_six_discord_leaders_safe"
        ) as enqueue:
            from app.services.bowl_six import _auto_update_single_slate

            note = _auto_update_single_slate(site_session, league_session, slate)
        enqueue.assert_called_once_with(site_session, league_session, slate)
        self.assertIn("updated 2 lineup", note or "")

    def test_discord_enqueue_skipped_for_non_current_week(self):
        slate = BowlSixSlate(
            id=58,
            league_slug="bowl-historical",
            week_start=date(2026, 5, 25),
            week_end=date(2026, 5, 31),
            status="scored",
        )
        site_session = MagicMock()
        league_session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six.is_current_bowl_six_week",
            return_value=False,
        ), unittest.mock.patch(
            "app.services.bowl_six_discord.maybe_enqueue_bowl_six_leaders_discord"
        ) as enqueue:
            from app.services.bowl_six import _enqueue_bowl_six_discord_leaders_safe

            _enqueue_bowl_six_discord_leaders_safe(site_session, league_session, slate)
        enqueue.assert_not_called()

    def test_open_slate_auto_update_enqueues_discord_even_without_final_games(self):
        slate = BowlSixSlate(
            id=57,
            league_slug="bowl-historical",
            week_start=date(1969, 3, 10),
            week_end=date(1969, 3, 16),
            status="open",
            lock_at=__import__("datetime").datetime(2099, 1, 1),
        )
        site_session = MagicMock()
        league_session = MagicMock()
        with unittest.mock.patch(
            "app.services.bowl_six._backfill_active_slate_final_markers_from_legacy_window",
            return_value=0,
        ), unittest.mock.patch(
            "app.services.bowl_six._record_current_calendar_final_markers_for_active_slate",
            return_value=0,
        ), unittest.mock.patch(
            "app.services.bowl_six.sync_slate_week_to_league_calendar", return_value=False
        ), unittest.mock.patch(
            "app.services.bowl_six.sync_slate_lock_status"
        ), unittest.mock.patch(
            "app.services.bowl_six.rs_game_ids_for_slate", return_value=[]
        ), unittest.mock.patch(
            "app.services.bowl_six.is_current_bowl_six_week",
            return_value=True,
        ), unittest.mock.patch(
            "app.services.bowl_six._enqueue_bowl_six_discord_leaders_safe"
        ) as enqueue:
            from app.services.bowl_six import _auto_update_single_slate

            _auto_update_single_slate(site_session, league_session, slate)
        enqueue.assert_called_once_with(site_session, league_session, slate)

    def test_auto_update_bowl_six_slates_retries_and_rolls_back_failed_slate(self):
        from sqlalchemy.exc import OperationalError

        from app.services.bowl_six import auto_update_bowl_six_slates

        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            BowlSixSlate(
                id=8,
                league_slug="bowl-historical",
                week_start=date(1969, 3, 10),
                week_end=date(1969, 3, 16),
                status="locked",
            )
        ]
        locked = OperationalError("stmt", {}, Exception("database is locked"))

        with unittest.mock.patch(
            "app.services.bowl_six.bowl_six_enabled", return_value=True
        ), unittest.mock.patch(
            "app.sqlite_retry.write_with_sqlite_retry", side_effect=locked
        ), unittest.mock.patch(
            "app.services.bowl_six.maybe_award_completed_bowl_six_seasons", return_value=0
        ):
            notes = auto_update_bowl_six_slates(session, session, "bowl-historical")

        session.rollback.assert_called_once()
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
