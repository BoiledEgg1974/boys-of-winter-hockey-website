"""Admin ownership panel and draft-pick transfer coverage."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.draft_pick_ownership import (
    sync_draft_pick_ownership_rollover_for_completed_drafts,
    transfer_approved_trade_draft_pick_rows,
)


class DraftPickOwnershipAdminTests(unittest.TestCase):
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

        with unittest.mock.patch(
            "app.services.draft_pick_ownership.ensure_draft_pick_ownership_panels",
            return_value=["ok"],
        ) as ensure_panels:
            out = sync_draft_pick_ownership_rollover_for_completed_drafts(
                site_session,
                MagicMock(),
                league_slug="bowl-historical",
            )
        self.assertEqual(out, ["ok"])
        self.assertEqual(panel.status, "completed")
        ensure_panels.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
