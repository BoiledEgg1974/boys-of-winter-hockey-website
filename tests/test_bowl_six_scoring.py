"""BOWL Six scoring and validation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.bowl_six_scoring import (
    position_kind,
    score_skater_line,
    slot_accepts_position,
)
from app.services.bowl_six import validate_lineup_picks
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


if __name__ == "__main__":
    unittest.main()
