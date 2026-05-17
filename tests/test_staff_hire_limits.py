"""Unit tests for staff hire calendar limits and catalog browse rules."""
from __future__ import annotations

import unittest
from datetime import date

from unittest.mock import patch

from app.services.staff_catalog import (
    _meets_browse_filter,
    build_staff_profile_view,
    compute_staff_role_overall,
    is_staff_assigned_to_main_league_team,
    staff_ids_assigned_to_fhm_teams,
)
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

    def test_staff_role_overall_1_100(self):
        attrs = {"coaching_g": 20.0, "coaching_defense": 20.0, "tactics": 20.0}
        rr = {"coach": "20", "scout": "10", "trainer": "10"}
        ovr = compute_staff_role_overall(
            attrs,
            ("coaching_g", "coaching_defense", "tactics"),
            ratings_row=rr,
            filter_key="head_coach",
        )
        self.assertIsNotNone(ovr)
        assert ovr is not None
        self.assertGreaterEqual(ovr, 90)
        self.assertLessEqual(ovr, 100)

    def test_staff_ids_assigned_to_fhm_teams(self):
        catalog = {
            "101": {"fhm_team_id": "5", "full_name": "On Team"},
            "102": {"fhm_team_id": "", "full_name": "Free"},
            "103": {"fhm_team_id": "99", "full_name": "Other"},
        }
        with patch("app.services.staff_catalog._load_catalog", return_value=catalog):
            ids = staff_ids_assigned_to_fhm_teams({"5"})
        self.assertEqual(ids, {"101"})

    def test_is_staff_assigned_to_main_league_team(self):
        prof = {"fhm_team_id": "12"}
        self.assertTrue(is_staff_assigned_to_main_league_team(prof, {"12", "34"}))
        self.assertFalse(is_staff_assigned_to_main_league_team(prof, {"99"}))
        self.assertFalse(is_staff_assigned_to_main_league_team({"fhm_team_id": ""}, {"12"}))

    def test_build_staff_profile_view_sections(self):
        profile = {
            "primary_bucket": "coaches",
            "ratings_row": {"coach": "18", "scout": "12", "trainer": "10"},
            "attrs": {
                "coaching_g": 12.0,
                "coaching_defense": 20.0,
                "evaluate_ability": 13.0,
                "trainer_skill": 1.0,
            },
        }
        view = build_staff_profile_view(profile)
        self.assertIsNotNone(view["primary_overall"])
        self.assertGreaterEqual(len(view["sections"]), 2)
        for section in view["sections"]:
            self.assertIsNotNone(section["overall_score"])
            self.assertGreaterEqual(section["overall_score"], 1)
            self.assertLessEqual(section["overall_score"], 100)


if __name__ == "__main__":
    unittest.main()
