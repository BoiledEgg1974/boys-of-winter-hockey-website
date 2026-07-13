"""Staff roster helpers and admin hire guards."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.staff_transactions import (
    _infer_staff_role_for_team,
    admin_hire_staff,
    sync_team_roster_from_fhm,
)


class StaffRosterSyncTest(unittest.TestCase):
    def test_infer_head_coach_among_multiple_coaches(self) -> None:
        profiles = [
            {"staff_fhm_id": "1", "primary_bucket": "coaches", "coach_rating": 18, "full_name": "A"},
            {"staff_fhm_id": "2", "primary_bucket": "coaches", "coach_rating": 20, "full_name": "B"},
        ]
        self.assertEqual(_infer_staff_role_for_team(profiles, profiles[1]), "head_coach")
        self.assertEqual(_infer_staff_role_for_team(profiles, profiles[0]), "assistant_coach")

    def test_infer_scout_and_trainer(self) -> None:
        scout = {"staff_fhm_id": "s", "primary_bucket": "scouts"}
        trainer = {"staff_fhm_id": "t", "primary_bucket": "trainers"}
        self.assertEqual(_infer_staff_role_for_team([scout], scout), "scout")
        self.assertEqual(_infer_staff_role_for_team([trainer], trainer), "trainer")

    def test_sync_team_roster_from_fhm_is_noop(self) -> None:
        session = MagicMock()
        added = sync_team_roster_from_fhm(
            session,
            league_slug="bowl-historical",
            team_id=5,
            season_start_year=1968,
            fhm_team_id="12",
        )
        self.assertEqual(added, 0)
        session.add.assert_not_called()

    @patch("app.services.staff_transactions._validate_hire_budget", return_value=None)
    @patch("app.services.staff_transactions._league_staff_defaults")
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_admin_hire_blocks_staff_assigned_in_fhm(
        self,
        mock_profile: MagicMock,
        _mock_active: MagicMock,
        _mock_defaults: MagicMock,
        _mock_budget: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_profile.return_value = {"staff_fhm_id": "99", "full_name": "Coach", "fhm_team_id": "12"}

        result = admin_hire_staff(
            session,
            league_slug="bowl-fantasy",
            season_start_year=1987,
            team_id=1,
            admin_user_id=2,
            staff_fhm_id="99",
            role="head_coach",
            contract_years=1,
        )

        self.assertFalse(result.ok)
        self.assertIn("Franchise Hockey Manager", result.message)

    @patch("app.services.staff_transactions._validate_hire_budget", return_value=None)
    @patch("app.services.staff_transactions._league_staff_defaults")
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_admin_hire_allows_unassigned_fhm_staff(
        self,
        mock_profile: MagicMock,
        _mock_active: MagicMock,
        mock_defaults: MagicMock,
        _mock_budget: MagicMock,
    ) -> None:
        from app.services.staff_salaries import StaffDefaultSalaries

        session = MagicMock()
        mock_profile.return_value = {"staff_fhm_id": "99", "full_name": "Coach", "fhm_team_id": ""}
        mock_defaults.return_value = StaffDefaultSalaries(
            head_coach=100,
            assistant_coaches=80,
            scouts=20,
            trainer=40,
        )

        result = admin_hire_staff(
            session,
            league_slug="bowl-fantasy",
            season_start_year=1987,
            team_id=1,
            admin_user_id=2,
            staff_fhm_id="99",
            role="head_coach",
            contract_years=1,
        )

        self.assertTrue(result.ok)
        session.add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
