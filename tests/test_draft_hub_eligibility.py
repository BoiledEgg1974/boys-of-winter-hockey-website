"""Draft Hub / Draft Eligible pool rules."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.draft_eligible_settings import (
    config_to_eligibility_params,
    default_draft_eligible_page_config,
)
from app.services.draft_hub_eligibility import (
    DRAFT_POOL_BIRTH_WINDOW,
    DRAFT_POOL_BORN_BEFORE,
    DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
    age_as_of,
    default_eligibility_for_league,
    draft_eligible_page_params_for_league,
    draft_eligible_timeline_year_for_league,
    effective_eligibility_params,
    eligible_player_ids,
    player_passes_born_before_rule,
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

    def test_fantasy_age_window_uses_active_season_start_year(self) -> None:
        params = replace(default_eligibility_for_league("bowl-fantasy"), timeline_year=1987)
        phaneuf_birth = date(1970, 4, 10)
        howe_birth = date(1969, 3, 31)
        self.assertFalse(player_passes_age_rules(phaneuf_birth, params))
        self.assertTrue(player_passes_age_rules(howe_birth, params))

    def test_cap_draft_eligible_page_uses_in_game_draft_year(self) -> None:
        self.assertEqual(
            draft_eligible_timeline_year_for_league("bowl-cap", 2025, 2026, 2025),
            2026,
        )
        params = draft_eligible_page_params_for_league("bowl-cap", 2026)
        self.assertEqual(params.timeline_year, 2026)
        self.assertEqual((params.min_age_years, params.min_anchor_month, params.min_anchor_day), (18, 9, 15))
        self.assertEqual((params.max_age_years, params.max_anchor_month, params.max_anchor_day), (20, 12, 31))

    def test_cap_draft_eligible_age_boundaries(self) -> None:
        params = draft_eligible_page_params_for_league("bowl-cap", 2026)
        turns_18_on_sep_15 = date(2008, 9, 15)
        turns_18_on_sep_16 = date(2008, 9, 16)
        turns_21_on_dec_31 = date(2005, 12, 31)
        turns_21_on_jan_1_after = date(2006, 1, 1)

        self.assertTrue(player_passes_age_rules(turns_18_on_sep_15, params))
        self.assertFalse(player_passes_age_rules(turns_18_on_sep_16, params))
        self.assertFalse(player_passes_age_rules(turns_21_on_dec_31, params))
        self.assertTrue(player_passes_age_rules(turns_21_on_jan_1_after, params))

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

    def test_born_before_pool_source_uses_exclusive_cutoff(self) -> None:
        self.assertTrue(player_passes_born_before_rule(date(1967, 12, 31), date(1968, 1, 1)))
        self.assertFalse(player_passes_born_before_rule(date(1968, 1, 1), date(1968, 1, 1)))

    def test_eligible_pool_supports_born_before_source(self) -> None:
        params = replace(
            default_eligibility_for_league("bowl-fantasy"),
            timeline_year=1988,
            pool_source=DRAFT_POOL_BORN_BEFORE,
            born_before_date=date(1968, 1, 1),
        )
        session = MagicMock()
        allowed = MagicMock(id=1, birth_date=date(1967, 12, 31), retired=False)
        blocked = MagicMock(id=2, birth_date=date(1968, 1, 1), retired=False)
        session.scalars.return_value.unique.return_value.all.return_value = [allowed, blocked]
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
            ids = eligible_player_ids(session, "bowl-fantasy", params)

        self.assertEqual(ids, [1])

    def test_historical_draft_eligible_page_source_resolves_to_public_page_pool(self) -> None:
        params = replace(
            default_eligibility_for_league("bowl-historical"),
            timeline_year=1968,
            pool_source=DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
            min_age_years=15,
            max_age_years=30,
        )

        effective = effective_eligibility_params("bowl-historical", params)
        expected = draft_eligible_page_params_for_league("bowl-historical", 1968)

        self.assertEqual(effective, expected)
        self.assertEqual(effective.pool_source, DRAFT_POOL_BIRTH_WINDOW)

    def test_historical_amateur_rules_use_birth_window_and_exclude_iron_curtain(self) -> None:
        before_window = MagicMock(birth_date=date(1949, 12, 27), nationality="Canada")
        window_start = MagicMock(birth_date=date(1949, 12, 28), nationality="Canada")
        window_end = MagicMock(birth_date=date(1950, 12, 31), nationality="Canada")
        after_window = MagicMock(birth_date=date(1951, 1, 1), nationality="Canada")
        russian_in_window = MagicMock(birth_date=date(1950, 6, 1), nationality="Russia")
        czech_in_window = MagicMock(birth_date=date(1950, 6, 1), nationality="Czech Republic")

        self.assertFalse(player_passes_historical_amateur_rules(before_window))
        self.assertTrue(player_passes_historical_amateur_rules(window_start))
        self.assertTrue(player_passes_historical_amateur_rules(window_end))
        self.assertFalse(player_passes_historical_amateur_rules(after_window))
        self.assertFalse(player_passes_historical_amateur_rules(russian_in_window))
        self.assertFalse(player_passes_historical_amateur_rules(czech_in_window))

    def test_historical_eligible_pool_applies_amateur_country_rules(self) -> None:
        params = config_to_eligibility_params(
            default_draft_eligible_page_config("bowl-historical"),
            timeline_year=1970,
        )
        session = MagicMock()
        allowed = MagicMock(
            id=1,
            birth_date=date(1950, 6, 1),
            retired=False,
            nationality="Canada",
        )
        outside_window = MagicMock(
            id=2,
            birth_date=date(1951, 1, 1),
            retired=False,
            nationality="Canada",
        )
        eastern_bloc = MagicMock(
            id=3,
            birth_date=date(1950, 6, 1),
            retired=False,
            nationality="Poland",
        )
        session.scalars.return_value.unique.return_value.all.return_value = [
            allowed,
            outside_window,
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

    def test_historical_draft_eligible_page_pool_uses_1950_window(self) -> None:
        params = draft_eligible_page_params_for_league("bowl-historical", 1969)
        session = MagicMock()
        window_amateur = MagicMock(
            id=1,
            birth_date=date(1950, 1, 1),
            retired=False,
            nationality="Canada",
        )
        before_window = MagicMock(
            id=2,
            birth_date=date(1949, 12, 27),
            retired=False,
            nationality="Canada",
        )
        eastern_bloc = MagicMock(
            id=3,
            birth_date=date(1950, 1, 1),
            retired=False,
            nationality="Soviet Union",
        )
        session.scalars.return_value.unique.return_value.all.return_value = [
            window_amateur,
            before_window,
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
