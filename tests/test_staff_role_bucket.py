"""Staff role bucketing for team Staff tabs."""
from __future__ import annotations

import unittest

from app.services.staff_catalog import STAFF_ROLES, staff_role_label
from app.services.team_staff_csv import (
    _staff_role_bucket,
    bucket_for_staff_role,
    merge_contract_staff_into_sections,
    rebucket_staff_sections_by_roles,
    staff_tab_row_from_profile,
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

    def test_staff_tab_row_from_profile_uses_role_bucket(self) -> None:
        row = staff_tab_row_from_profile(
            {
                "full_name": "Guillaume Dupont",
                "nationality": "Canada",
                "attrs": {"tactics": 18.0},
                "primary_bucket": "coaches",
            },
            staff_fhm_id="99",
            staff_name="Guillaume Dupont",
            role="assistant_coach",
        )
        self.assertEqual(row["staff_fhm_id"], "99")
        self.assertEqual(row["primary_bucket"], "coaches")
        self.assertEqual(row["attrs"]["tactics"], 18.0)

    def test_merge_contract_staff_adds_portal_only_hire(self) -> None:
        extra = staff_tab_row_from_profile(
            {"full_name": "Ray Wallace", "nationality": "Canada", "attrs": {}},
            staff_fhm_id="88",
            staff_name="Ray Wallace",
            role="assistant_coach",
        )
        c, s, t = merge_contract_staff_into_sections(
            [{"staff_fhm_id": "1", "full_name": "Pat Quinn"}],
            [],
            [],
            extra_rows=[extra],
        )
        self.assertEqual([row["staff_fhm_id"] for row in c], ["1", "88"])
        self.assertEqual(s, [])
        self.assertEqual(t, [])

    def test_merge_contract_staff_skips_existing_fhm_row(self) -> None:
        extra = staff_tab_row_from_profile(
            None,
            staff_fhm_id="1",
            staff_name="Pat Quinn",
            role="head_coach",
        )
        c, s, t = merge_contract_staff_into_sections(
            [{"staff_fhm_id": "1", "full_name": "Pat Quinn"}],
            [],
            [],
            extra_rows=[extra],
        )
        self.assertEqual(len(c), 1)
        self.assertEqual(s, [])
        self.assertEqual(t, [])


if __name__ == "__main__":
    unittest.main()
