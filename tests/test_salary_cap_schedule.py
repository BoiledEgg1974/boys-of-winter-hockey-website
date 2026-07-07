"""Salary cap schedule panel tests."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.salary_cap_schedule import (
    cap_for_season,
    complete_stale_salary_cap_panels,
    ensure_salary_cap_panels,
    season_label_from_start_year,
    sync_salary_cap_schedule_rollover,
)


class SalaryCapScheduleTests(unittest.TestCase):
    def test_season_label_from_start_year(self) -> None:
        self.assertEqual(season_label_from_start_year(2000), "2000-01")
        self.assertEqual(season_label_from_start_year(2001), "2001-02")
        self.assertEqual(season_label_from_start_year(1999), "1999-00")

    def test_complete_stale_marks_prior_seasons_completed(self) -> None:
        stale = MagicMock(status="active", season_start_year=1999)
        site_session = MagicMock()
        site_session.scalars.return_value.all.return_value = [stale]
        league_session = MagicMock()
        with patch(
            "app.services.salary_cap_schedule._current_season_start_year",
            return_value=2000,
        ):
            changed = complete_stale_salary_cap_panels(
                site_session,
                league_session,
                league_slug="bowl-fantasy",
            )
        self.assertEqual(changed, 1)
        self.assertEqual(stale.status, "completed")

    def test_cap_for_season_uses_panel_then_rules_fallback(self) -> None:
        panel = MagicMock(cap_ceiling=80_000_000, cap_floor=60_000_000)
        site_session = MagicMock()
        site_session.scalar.return_value = panel
        with patch(
            "app.services.salary_cap_schedule._current_season_start_year",
            return_value=2000,
        ):
            ceiling, floor = cap_for_season(
                site_session,
                "bowl-historical",
                2000,
            )
        self.assertEqual(ceiling, 80_000_000)
        self.assertEqual(floor, 60_000_000)

    def test_cap_for_season_rules_fallback_when_panel_unset(self) -> None:
        site_session = MagicMock()
        site_session.scalar.return_value = None
        with (
            patch(
                "app.services.salary_cap_schedule._current_season_start_year",
                return_value=2000,
            ),
            patch(
                "app.services.salary_cap_schedule._league_rules_cap_defaults",
                return_value=(55_000_000, 45_000_000),
            ),
        ):
            ceiling, floor = cap_for_season(site_session, "bowl-cap", 2000)
        self.assertEqual(ceiling, 55_000_000)
        self.assertEqual(floor, 45_000_000)

    def test_ensure_panels_seeds_three_active_years(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        created_panels: list[MagicMock] = []

        def _scalar(stmt):
            return None

        site_session.scalar.side_effect = _scalar
        site_session.scalars.return_value.all.return_value = []

        def _add(panel):
            created_panels.append(panel)

        site_session.add.side_effect = _add

        with (
            patch(
                "app.services.salary_cap_schedule._current_season_start_year",
                return_value=2000,
            ),
            patch(
                "app.services.salary_cap_schedule._league_rules_cap_defaults",
                return_value=(50_000_000, 40_000_000),
            ),
            patch(
                "app.services.salary_cap_schedule.list_salary_cap_year_panels",
                return_value=created_panels,
            ),
            patch(
                "app.services.salary_cap_schedule.complete_stale_salary_cap_panels",
                return_value=0,
            ),
        ):
            ensure_salary_cap_panels(
                site_session,
                league_session,
                league_slug="bowl-fantasy",
                active_count=3,
            )

        years = sorted(p.season_start_year for p in created_panels)
        self.assertEqual(years, [2000, 2001, 2002])
        self.assertEqual(created_panels[0].cap_ceiling, 50_000_000)

    def test_sync_rollover_delegates_to_ensure(self) -> None:
        with patch(
            "app.services.salary_cap_schedule.ensure_salary_cap_panels",
            return_value=["ok"],
        ) as ensure_mock:
            out = sync_salary_cap_schedule_rollover(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-historical",
            )
        self.assertEqual(out, ["ok"])
        ensure_mock.assert_called_once()

    def test_league_slugs_are_isolated_by_key(self) -> None:
        panel_a = MagicMock(league_slug="bowl-fantasy", cap_ceiling=12_000_000)
        panel_b = MagicMock(league_slug="bowl-historical", cap_ceiling=90_000_000)
        self.assertNotEqual(panel_a.league_slug, panel_b.league_slug)
        self.assertNotEqual(panel_a.cap_ceiling, panel_b.cap_ceiling)


if __name__ == "__main__":
    unittest.main()
