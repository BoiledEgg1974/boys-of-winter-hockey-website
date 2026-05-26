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
    player_passes_historical_amateur_rules,
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

    def test_eligible_pool_excludes_league_scoped_bowl_org_rights(self) -> None:
        params = replace(default_eligibility_for_league("bowl-fantasy"), timeline_year=1988)
        session = MagicMock()
        available_player = MagicMock(
            id=5259,
            birth_date=date(1969, 3, 31),
            retired=False,
        )
        session.scalars.return_value.unique.return_value.all.return_value = [available_player]
        with (
            patch(
                "app.services.draft_hub_eligibility.undrafted_nhl_bowl_player_subquery",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.draft_hub_eligibility.bowl_org_rights_player_ids_for_league",
                return_value=frozenset({5260}),
            ) as org_rights,
        ):
            ids = eligible_player_ids(session, "bowl-fantasy", params)
        org_rights.assert_called_once_with(session, "bowl-fantasy")
        self.assertEqual(ids, [5259])

    def test_historical_amateur_rules_exclude_1950_and_eastern_bloc(self) -> None:
        canadian_1949 = MagicMock(birth_date=date(1949, 12, 31), nationality="Canada")
        canadian_1950 = MagicMock(birth_date=date(1950, 1, 1), nationality="Canada")
        russian_1949 = MagicMock(birth_date=date(1949, 6, 1), nationality="Russia")
        czech_1949 = MagicMock(birth_date=date(1949, 6, 1), nationality="Czech Republic")

        self.assertTrue(player_passes_historical_amateur_rules(canadian_1949))
        self.assertFalse(player_passes_historical_amateur_rules(canadian_1950))
        self.assertFalse(player_passes_historical_amateur_rules(russian_1949))
        self.assertFalse(player_passes_historical_amateur_rules(czech_1949))

    def test_historical_eligible_pool_applies_amateur_country_rules(self) -> None:
        params = replace(default_eligibility_for_league("bowl-historical"), timeline_year=1970)
        session = MagicMock()
        allowed = MagicMock(
            id=1,
            birth_date=date(1949, 12, 31),
            retired=False,
            nationality="Canada",
        )
        too_young = MagicMock(
            id=2,
            birth_date=date(1950, 1, 1),
            retired=False,
            nationality="Canada",
        )
        eastern_bloc = MagicMock(
            id=3,
            birth_date=date(1949, 6, 1),
            retired=False,
            nationality="Poland",
        )
        session.scalars.return_value.unique.return_value.all.return_value = [
            allowed,
            too_young,
            eastern_bloc,
        ]
        with (
            patch(
                "app.services.draft_hub_eligibility.undrafted_nhl_bowl_player_subquery",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.draft_hub_eligibility.bowl_org_rights_player_ids_for_league",
                return_value=frozenset(),
            ),
        ):
            ids = eligible_player_ids(session, "bowl-historical", params)

        self.assertEqual(ids, [1])

    def test_season_age_reference_july_first(self) -> None:
        season = MagicMock(start_year=1987, end_year=1988)
        self.assertEqual(season_age_reference_date(season), date(1987, 7, 1))


if __name__ == "__main__":
    unittest.main()
