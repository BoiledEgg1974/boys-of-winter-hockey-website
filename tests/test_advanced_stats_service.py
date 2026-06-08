"""Advanced stats service unit tests."""
from __future__ import annotations

import unittest

from app.services.advanced_stats import pdo_band, sq_profile_from_counts, zone_start_pcts


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


if __name__ == "__main__":
    unittest.main()
