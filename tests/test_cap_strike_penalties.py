"""Cap strike tracker helpers and cap-only admin visibility checks."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.cap_strike_penalties import (
    active_cycle_year,
    apply_cycle_strikes_to_slots,
    save_cycle_strikes,
)


class CapStrikePenaltiesTest(unittest.TestCase):
    def test_active_cycle_year_uses_current_draft_year(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.cap_strike_penalties.in_game_draft_ownership_cutoff_year",
            return_value=2000,
        ) as cutoff:
            year = active_cycle_year(session, league_slug="bowl-cap")

        self.assertEqual(year, 2000)
        cutoff.assert_called_once_with(session, league_slug="bowl-cap")
        session.scalar.assert_not_called()

    def test_save_cycle_strikes_replaces_with_checked_rows(self) -> None:
        site = MagicMock()
        created, teams_with_any = save_cycle_strikes(
            site,
            league_slug="bowl-cap",
            cycle_year=2026,
            selected={101: {1, 3}, 202: {2}},
            admin_user_id=7,
        )
        self.assertEqual(created, 3)
        self.assertEqual(teams_with_any, 2)
        site.execute.assert_called_once()
        self.assertEqual(site.add.call_count, 3)

    def test_apply_cycle_strikes_marks_owned_pick(self) -> None:
        site = MagicMock()
        site.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[SimpleNamespace(team_id=10, strike_no=2)])),
            MagicMock(all=MagicMock(return_value=[SimpleNamespace(id=10, full_display_name=lambda: "Hamilton")])),
        ]
        slot = SimpleNamespace(team_id=10, penalty_pick=False)
        applied, warnings = apply_cycle_strikes_to_slots(
            site,
            league_slug="bowl-cap",
            cycle_year=2026,
            draft=SimpleNamespace(rounds=5),
            slots_by_orig_round={(10, 4): slot},
        )
        self.assertEqual(applied, 1)
        self.assertEqual(warnings, [])
        self.assertTrue(slot.penalty_pick)

    def test_apply_cycle_strikes_warns_when_pick_traded(self) -> None:
        site = MagicMock()
        site.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[SimpleNamespace(team_id=10, strike_no=1)])),
            MagicMock(all=MagicMock(return_value=[SimpleNamespace(id=10, full_display_name=lambda: "Hamilton")])),
        ]
        slot = SimpleNamespace(team_id=88, penalty_pick=False)
        applied, warnings = apply_cycle_strikes_to_slots(
            site,
            league_slug="bowl-cap",
            cycle_year=2026,
            draft=SimpleNamespace(rounds=5),
            slots_by_orig_round={(10, 5): slot},
        )
        self.assertEqual(applied, 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Hamilton Strike 1", warnings[0])
        self.assertFalse(slot.penalty_pick)

    def test_admin_home_link_is_cap_only(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("current_league_slug == 'bowl-cap'", text)
        self.assertIn("admin_rule_strikes", text)

    def test_admin_template_uses_two_column_card_grid(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_rule_strikes.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("cap-strike-admin__grid", text)
        self.assertIn("cap-strike-admin__team-card", text)
        self.assertIn("cap-strike-admin__checks", text)


if __name__ == "__main__":
    unittest.main()
