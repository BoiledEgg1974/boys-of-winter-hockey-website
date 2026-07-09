"""Record stat adjustment helpers."""
from __future__ import annotations

import unittest

from app.services.record_stat_adjustments import career_line_key


class RecordStatAdjustmentsTest(unittest.TestCase):
    def test_career_line_key_normalizes_fhm(self) -> None:
        key = career_line_key(player_id=5, season_year=1968, team_fhm_id=" 3 ", career_source="rs")
        self.assertEqual(key, (5, 1968, "3", "rs"))


if __name__ == "__main__":
    unittest.main()
