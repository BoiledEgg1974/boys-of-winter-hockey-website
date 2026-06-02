"""Admin ownership panel and draft-pick transfer coverage."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.draft_pick_ownership import (
    complete_stale_draft_pick_ownership_panels,
    default_draft_pick_ownership_start_year,
    draft_pick_teams_for_grid,
    ensure_draft_pick_ownership_panels,
    in_game_draft_ownership_cutoff_year,
    reactivate_current_draft_pick_ownership_panel_if_needed,
    sync_draft_pick_ownership_rollover_for_completed_drafts,
    transfer_approved_trade_draft_pick_rows,
)


class DraftPickOwnershipAdminTests(unittest.TestCase):
    def test_draft_pick_grid_teams_are_alphabetical(self) -> None:
        def team(tid: int, name: str, abbr: str):
            row = MagicMock(id=tid, fhm_team_id=str(tid), abbreviation=abbr)
            row.full_display_name.return_value = name
            return row

        league_session = MagicMock()
        league_session.scalars.return_value.all.return_value = [
            team(3, "Winnipeg Thunder", "WIC"),
            team(1, "Hamilton Steel", "HAM"),
            team(2, "Helsinki Jokerit", "HEL"),
        ]
        with unittest.mock.patch("app.services.draft_pick_ownership.is_main_league_team", return_value=True):
            rows = draft_pick_teams_for_grid(league_session)
        self.assertEqual([r.abbreviation for r in rows], ["HAM", "HEL", "WIC"])

    def test_transfer_approved_trade_draft_pick_rows_moves_row_owner(self) -> None:
        league_session = MagicMock()
        league_session.scalars.return_value.all.return_value = [
            MagicMock(id=10, fhm_team_id="5"),
            MagicMock(id=11, fhm_team_id="8"),
        ]
        row = MagicMock(
            id=42,
            league_slug="bowl-historical",
            owner_team_id=10,
            owner_team_fhm_id=5,
            draft_year=1991,
            round=1,
            original_team_fhm_id=5,
        )
        site_session = MagicMock()
        site_session.get.return_value = row

        changed = transfer_approved_trade_draft_pick_rows(
            site_session,
            league_session,
            league_slug="bowl-historical",
            from_team_id=10,
            to_team_id=11,
            left_out=["dpick:42"],
            right_out=[],
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(int(row.owner_team_id), 11)
        self.assertEqual(int(row.owner_team_fhm_id), 8)

    def test_rollover_marks_completed_years_and_rehydrates_active_panels(self) -> None:
        site_session = MagicMock()
        completed_scalars = MagicMock()
        completed_scalars.all.return_value = [1991]
        panel = MagicMock(status="active")
        panel_scalars = MagicMock()
        panel_scalars.all.return_value = [panel]
        site_session.scalars.side_effect = [completed_scalars, panel_scalars]

        with (
            unittest.mock.patch(
                "app.services.draft_pick_ownership.complete_stale_draft_pick_ownership_panels",
                return_value=0,
            ),
            unittest.mock.patch(
                "app.services.draft_pick_ownership.ensure_draft_pick_ownership_panels",
                return_value=["ok"],
            ) as ensure_panels,
        ):
            out = sync_draft_pick_ownership_rollover_for_completed_drafts(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
            )
        self.assertEqual(out, ["ok"])
        self.assertEqual(panel.status, "completed")
        ensure_panels.assert_called_once()

    def test_july_rollover_completes_prior_in_game_draft_year_panels(self) -> None:
        site_session = MagicMock()
        stale = MagicMock(status="active", draft_year=1968)
        current = MagicMock(status="active", draft_year=1969)
        site_session.scalars.return_value.all.return_value = [stale]

        with unittest.mock.patch(
            "app.services.draft_pick_ownership.in_game_draft_ownership_cutoff_year",
            return_value=1969,
        ):
            changed = complete_stale_draft_pick_ownership_panels(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
            )

        self.assertEqual(changed, 1)
        self.assertEqual(stale.status, "completed")
        self.assertEqual(current.status, "active")

    def test_default_year_does_not_lag_behind_current_in_game_season(self) -> None:
        site_session = MagicMock()
        site_session.scalar.return_value = 1999
        with unittest.mock.patch(
            "app.services.draft_pick_ownership.in_game_draft_ownership_cutoff_year",
            return_value=2000,
        ):
            year = default_draft_pick_ownership_start_year(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
            )

        self.assertEqual(year, 2000)

    def test_cutoff_uses_season_end_year_as_draft_year(self) -> None:
        season = MagicMock(start_year=1999, end_year=2000)
        league_session = MagicMock()
        league_session.scalar.return_value = season
        with unittest.mock.patch(
            "app.services.draft_pick_ownership.season_with_imported_data_fallback",
            return_value=None,
        ):
            cutoff = in_game_draft_ownership_cutoff_year(league_session)

        self.assertEqual(cutoff, 2000)

    def test_historical_cutoff_uses_season_start_year_as_draft_year(self) -> None:
        season = MagicMock(start_year=1969, end_year=1970)
        league_session = MagicMock()
        league_session.scalar.return_value = season
        with unittest.mock.patch(
            "app.services.draft_pick_ownership.season_with_imported_data_fallback",
            return_value=None,
        ):
            cutoff = in_game_draft_ownership_cutoff_year(
                league_session,
                league_slug="bowl-historical",
            )

        self.assertEqual(cutoff, 1969)

    def test_current_draft_year_panel_reactivates_after_rule_change(self) -> None:
        site_session = MagicMock()
        panel = MagicMock(status="completed")
        site_session.scalar.return_value = panel
        with unittest.mock.patch(
            "app.services.draft_pick_ownership.in_game_draft_ownership_cutoff_year",
            return_value=1969,
        ):
            changed = reactivate_current_draft_pick_ownership_panel_if_needed(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
            )

        self.assertTrue(changed)
        self.assertEqual(panel.status, "active")

    def test_ensure_panels_trims_extra_active_future_years(self) -> None:
        panels = [
            MagicMock(id=1, draft_year=1969, display_order=1, status="active"),
            MagicMock(id=2, draft_year=1970, display_order=2, status="active"),
            MagicMock(id=3, draft_year=1971, display_order=3, status="active"),
            MagicMock(id=4, draft_year=1972, display_order=4, status="active"),
        ]
        site_session = MagicMock()
        with (
            unittest.mock.patch(
                "app.services.draft_pick_ownership.complete_stale_draft_pick_ownership_panels",
                return_value=0,
            ),
            unittest.mock.patch(
                "app.services.draft_pick_ownership.reactivate_current_draft_pick_ownership_panel_if_needed",
                return_value=False,
            ),
            unittest.mock.patch(
                "app.services.draft_pick_ownership.list_draft_pick_ownership_year_panels",
                side_effect=[panels, panels, panels],
            ),
            unittest.mock.patch(
                "app.services.draft_pick_ownership.draft_pick_teams_for_grid",
                return_value=[],
            ),
        ):
            ensure_draft_pick_ownership_panels(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
                active_count=3,
            )

        self.assertEqual(panels[3].status, "completed")

    def test_admin_template_includes_owner_dropdown_grid(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "admin_draft_pick_ownership.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("name=\"owner_{{ team_row.team_fhm_id }}_{{ cell.round }}\"", text)
        self.assertIn("Draft Pick Ownership", text)

    def test_admin_route_uses_era_aware_logos_for_all_leagues(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("logo_year = int(panel.draft_year) - 1", text)
        self.assertIn("team_logo_url_for_season_context(team, int(logo_year))", text)
        self.assertNotIn('if slug == "bowl-fantasy":\n            return logo_bundle.team_logo_url_present_franchise(team)', text)

    def test_site_admin_home_links_to_draft_pick_ownership(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "admin_site_home.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_draft_pick_ownership", text)

    def test_team_depth_template_shows_year_grouped_draft_panels(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "team.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("team-depth-draft-years", text)
        self.assertIn("year_block.year", text)
        self.assertIn("OVR", text)

    def test_team_depth_chart_roster_status_colors_are_explicit(self) -> None:
        css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "site.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".team-depth-player.is-main", css)
        self.assertIn("color: #4ade80", css)
        self.assertIn(".team-depth-player.is-minor", css)
        self.assertIn("color: #60a5fa", css)
        self.assertIn(".team-depth-player.is-rights", css)
        self.assertIn("color: #f87171", css)

    def test_team_depth_builder_prefers_minor_contract_over_rights_only(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "main.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("player_ids_from_player_rights_csv_for_team", text)
        self.assertIn("is_main = pl.current_team_id == team.id", text)
        self.assertNotIn("is_main = (fhm_pid in main_line_player_ids)", text)
        minor_idx = text.index('roster_status = "minor"')
        rights_idx = text.index('roster_status = "rights"')
        self.assertLess(minor_idx, rights_idx)


if __name__ == "__main__":
    unittest.main()
