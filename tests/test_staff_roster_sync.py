"""Staff roster sync from FHM CSV assignments."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.staff_transactions import (
    _infer_staff_role_for_team,
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

    @patch("app.services.staff_transactions.list_staff_profiles_for_fhm_team")
    @patch("app.services.staff_transactions.active_roster_for_team")
    @patch("app.services.staff_transactions._active_roster_entry")
    def test_sync_adds_missing_fhm_staff(
        self,
        mock_active_entry: MagicMock,
        mock_active_roster: MagicMock,
        mock_list_profiles: MagicMock,
    ) -> None:
        mock_active_roster.return_value = []
        mock_active_entry.return_value = None
        mock_list_profiles.return_value = [
            {
                "staff_fhm_id": "99",
                "full_name": "Pat Burns",
                "primary_bucket": "coaches",
                "coach_rating": 19,
            }
        ]
        session = MagicMock()
        added = sync_team_roster_from_fhm(
            session,
            league_slug="bowl-historical",
            team_id=5,
            season_start_year=1968,
            fhm_team_id="12",
        )
        self.assertEqual(added, 1)
        session.add.assert_called_once()
        session.flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
