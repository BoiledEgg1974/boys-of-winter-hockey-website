"""Admin-configurable Draft Eligible birth-date rules."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.draft_eligible_settings import (
    DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
    DraftEligiblePageConfig,
    config_to_eligibility_params,
    default_draft_eligible_page_config,
    format_draft_eligible_summary,
    save_draft_eligible_page_config,
)
from app.services.draft_hub_eligibility import (
    DRAFT_POOL_BIRTH_WINDOW,
    eligible_player_ids,
)


class DraftEligibleSettingsTests(unittest.TestCase):
    def test_historical_defaults_use_birth_window(self) -> None:
        config = default_draft_eligible_page_config("bowl-historical")
        self.assertEqual(config.pool_mode, DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW)
        self.assertEqual(config.birth_start, date(1949, 12, 28))
        self.assertEqual(config.birth_end, date(1950, 12, 31))
        self.assertTrue(config.exclude_eastern_bloc)

    def test_config_maps_to_eligibility_params(self) -> None:
        config = DraftEligiblePageConfig(
            pool_mode=DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
            birth_start=date(1951, 1, 1),
            birth_end=date(1951, 12, 31),
            exclude_eastern_bloc=True,
            min_age_years=18,
            min_anchor_month=9,
            min_anchor_day=15,
            max_age_years=20,
            max_anchor_month=12,
            max_anchor_day=31,
        )
        params = config_to_eligibility_params(config, timeline_year=1970)
        self.assertEqual(params.pool_source, DRAFT_POOL_BIRTH_WINDOW)
        self.assertEqual(params.birth_window_start, date(1951, 1, 1))
        self.assertEqual(params.birth_window_end, date(1951, 12, 31))

    def test_summary_mentions_birth_window(self) -> None:
        config = default_draft_eligible_page_config("bowl-historical")
        text = format_draft_eligible_summary(
            config,
            league_slug="bowl-historical",
            timeline_year=1970,
        )
        self.assertIn("December 28, 1949", text)
        self.assertIn("December 31, 1950", text)
        self.assertIn("Iron Curtain", text)

    def test_save_writes_rule_values(self) -> None:
        session = MagicMock()
        stored: dict[tuple[str, str], str] = {}
        session.scalar.return_value = None

        def _capture_add(row):
            stored[(row.league_slug, row.rule_key)] = row.rule_value

        session.add.side_effect = _capture_add
        with patch(
            "app.services.draft_eligible_settings.ensure_league_rules",
        ), patch(
            "app.services.draft_eligible_settings.commit_with_sqlite_retry",
        ), patch(
            "app.services.draft_eligible_settings.ensure_draft_eligible_rule_rows",
        ):
            config = DraftEligiblePageConfig(
                pool_mode=DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
                birth_start=date(1952, 3, 1),
                birth_end=date(1952, 8, 31),
                exclude_eastern_bloc=False,
                min_age_years=18,
                min_anchor_month=9,
                min_anchor_day=15,
                max_age_years=20,
                max_anchor_month=12,
                max_anchor_day=31,
            )
            save_draft_eligible_page_config(session, "bowl-historical", config, updated_by_user_id=1)
        self.assertEqual(stored[("bowl-historical", "draft_eligible_birth_start")], "1952-03-01")
        self.assertEqual(stored[("bowl-historical", "draft_eligible_birth_end")], "1952-08-31")
        self.assertEqual(stored[("bowl-historical", "draft_eligible_exclude_eastern_bloc")], "false")

    def test_custom_birth_window_filters_pool(self) -> None:
        config = DraftEligiblePageConfig(
            pool_mode=DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
            birth_start=date(1950, 1, 1),
            birth_end=date(1950, 12, 31),
            exclude_eastern_bloc=True,
            min_age_years=18,
            min_anchor_month=9,
            min_anchor_day=15,
            max_age_years=20,
            max_anchor_month=12,
            max_anchor_day=31,
        )
        params = config_to_eligibility_params(config, timeline_year=1970)
        session = MagicMock()
        allowed = MagicMock(
            id=1,
            birth_date=date(1950, 6, 1),
            retired=False,
            nationality="Canada",
        )
        blocked = MagicMock(
            id=2,
            birth_date=date(1949, 1, 1),
            retired=False,
            nationality="Canada",
        )
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
            ids = eligible_player_ids(session, "bowl-historical", params)
        self.assertEqual(ids, [1])


if __name__ == "__main__":
    unittest.main()
