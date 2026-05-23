"""Draft Hub / Draft Eligible pool rules."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.draft_hub_eligibility import (
    age_as_of,
    default_eligibility_for_league,
    eligible_player_ids,
    player_passes_age_rules,
)
from app.services.seasons import season_age_reference_date


class DraftHubEligibilityTest(unittest.TestCase):
    def test_age_as_of_before_birthday_in_year(self) -> None:
        birth = date(1970, 4, 10)
        july = date(1987, 7, 1)
        dec31 = date(1988, 12, 31)
        self.assertEqual(age_as_of(birth, july), 17)
        self.assertEqual(age_as_of(birth, dec31), 18)

    def test_fantasy_cap_age_window_uses_active_season_start_year(self) -> None:
        params = replace(default_eligibility_for_league("bowl-fantasy"), timeline_year=1987)
        phaneuf_birth = date(1970, 4, 10)
        howe_birth = date(1969, 3, 31)
        self.assertFalse(player_passes_age_rules(phaneuf_birth, params))
        self.assertTrue(player_passes_age_rules(howe_birth, params))

    def test_eligible_pool_uses_nhl_org_rights_not_raw_exports(self) -> None:
        params = replace(default_eligibility_for_league("bowl-fantasy"), timeline_year=1988)
        session = MagicMock()
        player = MagicMock(
            id=5259,
            birth_date=date(1969, 3, 31),
            retired=False,
        )
        session.scalars.return_value.unique.return_value.all.return_value = [player]
        with (
            patch(
                "app.services.draft_hub_eligibility.undrafted_nhl_bowl_player_subquery",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.draft_hub_eligibility.bowl_nhl_org_rights_player_ids",
                return_value=frozenset(),
            ) as nhl_rights,
        ):
            ids = eligible_player_ids(session, "bowl-fantasy", params)
        nhl_rights.assert_called_once()
        self.assertEqual(ids, [5259])

    def test_season_age_reference_july_first(self) -> None:
        season = MagicMock(start_year=1987, end_year=1988)
        self.assertEqual(season_age_reference_date(season), date(1987, 7, 1))


if __name__ == "__main__":
    unittest.main()
