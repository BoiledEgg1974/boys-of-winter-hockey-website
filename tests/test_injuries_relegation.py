"""Injury classification and Discord delta helpers."""
from __future__ import annotations

import unittest

from app.services.injuries import classify_injury_status
from app.services.injury_discord import diff_injury_snapshots


class InjuryStatusTests(unittest.TestCase):
    def test_short_max_days_is_day_to_day(self) -> None:
        self.assertEqual(classify_injury_status(5, 3, 5), "day_to_day")

    def test_long_recovery_is_out(self) -> None:
        self.assertEqual(classify_injury_status(200, 90, 160), "out")

    def test_diff_detects_add_and_remove(self) -> None:
        prev = [{"player_id": 1, "team_id": 2, "injury_name": "Concussion", "recovery_days": 10}]
        cur = [{"player_id": 2, "team_id": 3, "injury_name": "Sprain", "recovery_days": 7}]
        delta = diff_injury_snapshots(prev, cur)
        self.assertEqual(len(delta["added"]), 1)
        self.assertEqual(len(delta["removed"]), 1)
        self.assertEqual(delta["added"][0]["player_id"], 2)


if __name__ == "__main__":
    unittest.main()
