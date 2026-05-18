"""BOWL Six scoring and validation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.bowl_six_scoring import (
    position_kind,
    score_skater_line,
    slot_accepts_position,
)
from datetime import date

from app.services.bowl_six import (
    eastern_naive_from_utc_naive,
    lock_at_display_eastern,
    lock_at_iso_z,
    parse_lock_at_eastern_form,
    slate_lock_ui,
    slate_week_rs_games_complete,
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

    def test_slate_week_complete_requires_all_final_rs_games(self):
        slate = BowlSixSlate(
            league_slug="bowl-cap",
            week_start=date(2026, 5, 18),
            week_end=date(2026, 5, 24),
            lock_at=__import__("datetime").datetime(2026, 5, 18),
            status="locked",
        )
        league_session = MagicMock()
        g1 = MagicMock(status="final", game_type="RS")
        g2 = MagicMock(status="scheduled", game_type="RS")
        with unittest.mock.patch(
            "app.services.bowl_six.rs_games_in_slate_week", return_value=[g1, g2]
        ):
            self.assertFalse(slate_week_rs_games_complete(league_session, slate))
        with unittest.mock.patch(
            "app.services.bowl_six.rs_games_in_slate_week",
            return_value=[g1, MagicMock(status="final", game_type="RS")],
        ):
            self.assertTrue(slate_week_rs_games_complete(league_session, slate))
        with unittest.mock.patch("app.services.bowl_six.rs_games_in_slate_week", return_value=[]):
            self.assertFalse(slate_week_rs_games_complete(league_session, slate))

    def test_parse_lock_at_eastern_form(self):
        # May 19 2026 8:30 PM EDT (UTC-4) -> May 20 00:30 UTC
        self.assertEqual(
            parse_lock_at_eastern_form("2026-05-19", "20:30"),
            __import__("datetime").datetime(2026, 5, 20, 0, 30),
        )
        self.assertIsNone(parse_lock_at_eastern_form("", "12:00"))

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


if __name__ == "__main__":
    unittest.main()
