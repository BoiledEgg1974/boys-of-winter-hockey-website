"""Staff role bucketing for team Staff tabs (coach/scout/trainer)."""
from __future__ import annotations

import unittest

from app.services.team_staff_csv import (
    _staff_role_bucket,
    bucket_for_staff_role,
    rebucket_staff_sections_by_roles,
)


class StaffRoleBucketTest(unittest.TestCase):
    def test_clear_winner_by_aptitude(self) -> None:
        self.assertEqual(
            _staff_role_bucket({"coach": "18", "scout": "10", "trainer": "5"}),
            "coaches",
        )
        self.assertEqual(
            _staff_role_bucket({"coach": "10", "scout": "18", "trainer": "5"}),
            "scouts",
        )
        self.assertEqual(
            _staff_role_bucket({"coach": "10", "scout": "8", "trainer": "19"}),
            "trainers",
        )

    def test_aptitude_tie_prefers_coaches_as_default_only(self) -> None:
        # Without a contract role, dual Coach/Scout aptitude defaults to coaches.
        # Admins override via the Staff tab role dropdown.
        self.assertEqual(
            _staff_role_bucket({"coach": "20", "scout": "20", "trainer": "1"}),
            "coaches",
        )

    def test_bucket_for_staff_role(self) -> None:
        self.assertEqual(bucket_for_staff_role("scout"), "scouts")
        self.assertEqual(bucket_for_staff_role("head_coach"), "coaches")
        self.assertEqual(bucket_for_staff_role("assistant_coach"), "coaches")
        self.assertEqual(bucket_for_staff_role("trainer"), "trainers")
        self.assertIsNone(bucket_for_staff_role(""))
        self.assertIsNone(bucket_for_staff_role(None))

    def test_rebucket_prefers_contract_role(self) -> None:
        coaches = [
            {
                "staff_fhm_id": "2319",
                "full_name": "Rich Brown",
                "primary_bucket": "coaches",
            }
        ]
        c, s, t = rebucket_staff_sections_by_roles(
            coaches,
            [],
            [],
            role_by_staff_id={"2319": "scout"},
        )
        self.assertEqual(c, [])
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["staff_fhm_id"], "2319")
        self.assertEqual(s[0]["primary_bucket"], "scouts")
        self.assertEqual(t, [])

    def test_rebucket_contract_coach_wins_over_csv_scout(self) -> None:
        rows = [
            {
                "staff_fhm_id": "99",
                "full_name": "Manual Coach",
                "primary_bucket": "scouts",
            }
        ]
        c, s, t = rebucket_staff_sections_by_roles(
            [],
            rows,
            [],
            role_by_staff_id={"99": "assistant_coach"},
        )
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["primary_bucket"], "coaches")
        self.assertEqual(s, [])
        self.assertEqual(t, [])


if __name__ == "__main__":
    unittest.main()
