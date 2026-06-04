"""Undrafted prospects configuration."""
from __future__ import annotations

import unittest

from app.config import undrafted_prospects_age_filter_options, undrafted_prospects_max_age


class UndraftedProspectsConfigTest(unittest.TestCase):
    def test_all_leagues_allow_age_22_and_younger(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            with self.subTest(slug=slug):
                self.assertEqual(undrafted_prospects_max_age(slug), 22)
                self.assertEqual(undrafted_prospects_age_filter_options(slug), tuple(range(22, 14, -1)))


if __name__ == "__main__":
    unittest.main()
