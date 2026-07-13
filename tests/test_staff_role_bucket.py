"""Staff role bucketing for team Staff tabs."""
from __future__ import annotations

import unittest

from app.services.staff_catalog import STAFF_ROLES, staff_role_label
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
        self.assertEqual(
            _staff_role_bucket({"coach": "20", "scout": "20", "trainer": "1"}),
            "coaches",
        )

    def test_portal_roles_in_catalog(self) -> None:
        self.assertIn("team_owner", STAFF_ROLES)
        self.assertIn("general_manager", STAFF_ROLES)
        self.assertEqual(staff_role_label("team_owner"), "Team Owner")
        self.assertEqual(staff_role_label("general_manager"), "General Manager")

    def test_bucket_for_staff_role(self) -> None:
        self.assertEqual(bucket_for_staff_role("scout"), "scouts")
        self.assertEqual(bucket_for_staff_role("head_coach"), "coaches")
        self.assertEqual(bucket_for_staff_role("assistant_coach"), "coaches")
        self.assertEqual(bucket_for_staff_role("trainer"), "trainers")
        self.assertEqual(bucket_for_staff_role("general_manager"), "managers")
        self.assertEqual(bucket_for_staff_role("team_owner"), "owners")
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
        c, s, t, m, o = rebucket_staff_sections_by_roles(
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
        self.assertEqual(m, [])
        self.assertEqual(o, [])

    def test_rebucket_general_manager(self) -> None:
        coaches = [
            {
                "staff_fhm_id": "100",
                "full_name": "Sample GM",
                "primary_bucket": "coaches",
            }
        ]
        c, s, t, m, o = rebucket_staff_sections_by_roles(
            coaches,
            [],
            [],
            role_by_staff_id={"100": "general_manager"},
        )
        self.assertEqual(c, [])
        self.assertEqual(s, [])
        self.assertEqual(t, [])
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["primary_bucket"], "managers")
        self.assertEqual(o, [])

    def test_rebucket_team_owner(self) -> None:
        coaches = [
            {
                "staff_fhm_id": "289",
                "full_name": "Jeremy Jacobs",
                "primary_bucket": "coaches",
            }
        ]
        c, s, t, m, o = rebucket_staff_sections_by_roles(
            coaches,
            [],
            [],
            role_by_staff_id={"289": "team_owner"},
        )
        self.assertEqual(c, [])
        self.assertEqual(s, [])
        self.assertEqual(t, [])
        self.assertEqual(m, [])
        self.assertEqual(len(o), 1)
        self.assertEqual(o[0]["primary_bucket"], "owners")


if __name__ == "__main__":
    unittest.main()
