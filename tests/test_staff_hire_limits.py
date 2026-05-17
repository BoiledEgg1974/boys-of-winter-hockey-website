"""Unit tests for staff hire calendar limits and catalog browse rules."""
from __future__ import annotations

import unittest
from datetime import date

from app.services.staff_catalog import _meets_browse_filter, compute_staff_role_overall
from app.services.staff_hire_limits import hire_limit_for_calendar_date, hire_window_label


class StaffHireLimitsTest(unittest.TestCase):
    def test_hire_limit_july_through_oct1(self):
        self.assertEqual(hire_limit_for_calendar_date(date(2025, 7, 1)), 3)
        self.assertEqual(hire_limit_for_calendar_date(date(2025, 9, 30)), 3)
        self.assertEqual(hire_limit_for_calendar_date(date(2025, 10, 1)), 3)

    def test_hire_limit_oct2_through_june(self):
        self.assertEqual(hire_limit_for_calendar_date(date(2025, 10, 2)), 1)
        self.assertEqual(hire_limit_for_calendar_date(date(2026, 6, 30)), 1)
        self.assertEqual(hire_limit_for_calendar_date(date(2026, 1, 15)), 1)

    def test_hire_window_labels(self):
        self.assertIn("3 hires", hire_window_label(date(2025, 8, 1)))
        self.assertIn("1 hire", hire_window_label(date(2025, 11, 1)))

    def test_coach_browse_shared_pool(self):
        rr = {"coach": "18", "scout": "10", "trainer": "10"}
        self.assertTrue(_meets_browse_filter(rr, "head_coach", 16.0))
        self.assertTrue(_meets_browse_filter(rr, "assistant_coach", 16.0))

    def test_scout_browse_requires_scout_bucket(self):
        rr = {"coach": "20", "scout": "18", "trainer": "10"}
        self.assertFalse(_meets_browse_filter(rr, "scout", 16.0))
        rr_scout = {"coach": "10", "scout": "18", "trainer": "10"}
        self.assertTrue(_meets_browse_filter(rr_scout, "scout", 16.0))

    def test_staff_role_overall_mean(self):
        attrs = {"coaching_g": 18.0, "coaching_defense": 16.0, "tactics": 20.0}
        self.assertEqual(compute_staff_role_overall(attrs, ("coaching_g", "coaching_defense", "tactics")), 18)


if __name__ == "__main__":
    unittest.main()
