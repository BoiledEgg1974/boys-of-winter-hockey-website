"""Draft-pick ownership helpers (Trade Market / Trade Tool)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app import create_app
from app.config import make_league_config
from app.services.draft_pick_ownership import (
    describe_draft_pick_row,
    draft_pick_drag_key,
)
from app.services.trade_tool import format_ledger_summary_for_discord


class DraftPickOwnershipHelperTest(unittest.TestCase):
    def test_describe_draft_pick_label(self) -> None:
        row = MagicMock(
            draft_year=2000,
            round=2,
            original_team_fhm_id=5,
            owner_team_fhm_id=12,
        )
        orig = MagicMock(abbreviation="STL")
        owner = MagicMock(abbreviation="BOS")
        label = describe_draft_pick_row(row, original_team=orig, owner_team=owner)
        self.assertIn("2000", label)
        self.assertIn("2", label)
        self.assertTrue("STL" in label or "BOS" in label)

    def test_drag_key_format(self) -> None:
        self.assertEqual(draft_pick_drag_key(42), "dpick:42")

    def test_discord_trade_summary_links_players_and_teams(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        from_team = MagicMock(slug="oakland-seals")
        from_team.full_display_name.return_value = "Oakland Seals"
        to_team = MagicMock(slug="boston-bruins")
        to_team.full_display_name.return_value = "Boston Bruins"
        player = MagicMock(id=77, position="G", full_name="Gary Simmons")
        session = MagicMock()
        session.get.return_value = player

        with app.app_context():
            text = format_ledger_summary_for_discord(
                session,
                from_team,
                to_team,
                ["player:77"],
                [],
                league_slug="bowl-historical",
            )

        self.assertIn("[Oakland Seals](https://www.bowlhockey.com/bowl-historical/team/oakland-seals)", text)
        self.assertIn("[Boston Bruins](https://www.bowlhockey.com/bowl-historical/team/boston-bruins)", text)
        self.assertIn("[Gary Simmons](https://www.bowlhockey.com/bowl-historical/player/77)", text)


if __name__ == "__main__":
    unittest.main()
