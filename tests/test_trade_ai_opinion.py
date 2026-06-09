"""Trade AI prompt construction."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.trade_ai_opinion import (
    _guard_hypothetical_trade_consistency,
    build_logged_trade_prompt_block,
    build_trade_prompt_block,
)
from app.services.trade_log import TradeLogRow


class TradeAiOpinionPromptTests(unittest.TestCase):
    def test_manual_sends_blocks_are_rewritten_as_visible_card_acquisitions(self) -> None:
        row = TradeLogRow(
            sort_at=datetime(2026, 5, 12),
            trade_date=date(2026, 5, 12),
            team_a=None,
            team_b=None,
            title="Trade: Montreal Canadiens ↔ Boston Bruins",
            body=(
                "Montreal Canadiens sends:\n"
                "Ron Schock\n"
                "1970 1st Round (MTL)\n\n"
                "Boston Bruins sends:\n"
                "Bobby Hull\n"
                "Harry Howell"
            ),
            source="manual",
            team_a_label="Montreal Canadiens",
            team_b_label="Boston Bruins",
            entry_id=1,
        )

        prompt = build_logged_trade_prompt_block(row)

        self.assertIn("Visible trade card interpretation (authoritative):", prompt)
        self.assertIn(
            "Montreal Canadiens acquired: Bobby Hull; Harry Howell",
            prompt,
        )
        self.assertIn(
            "Boston Bruins acquired: Ron Schock; 1970 1st Round (MTL)",
            prompt,
        )
        self.assertNotIn("Summary / details:", prompt)

    def test_logged_trade_tool_sends_to_blocks_match_visible_card_direction(self) -> None:
        row = TradeLogRow(
            sort_at=datetime(2026, 6, 8),
            trade_date=date(2026, 6, 8),
            team_a=None,
            team_b=None,
            title="Trade: Buffalo Sabres ↔ New York Rangers",
            body=(
                "New York Rangers sends to Buffalo Sabres:\n"
                "  • D Radek Martinek\n"
                "  • D Steve Duchesne\n\n"
                "Buffalo Sabres sends to New York Rangers:\n"
                "  • LW Jaroslav Bednar\n"
                "  • 2001 BUF 3rd Round pick\n\n"
                "Approved by the league office."
            ),
            source="site",
            team_a_label="Buffalo Sabres",
            team_b_label="New York Rangers",
            article_id=536,
        )

        prompt = build_logged_trade_prompt_block(row)

        self.assertIn("Buffalo Sabres acquired: D Radek Martinek; D Steve Duchesne", prompt)
        self.assertIn("New York Rangers acquired: LW Jaroslav Bednar; 2001 BUF 3rd Round pick", prompt)
        self.assertNotIn("Summary / details:", prompt)

    def test_hypothetical_prompt_includes_authoritative_received_direction(self) -> None:
        session = SimpleNamespace()
        coyotes = SimpleNamespace(full_display_name=lambda: "Phoenix Coyotes")
        thrashers = SimpleNamespace(full_display_name=lambda: "Atlanta Thrashers")

        prompt = build_trade_prompt_block(
            session,
            coyotes,
            thrashers,
            ["mpleft:1:abc123"],
            ["mpright:2:def456"],
            "",
        )

        self.assertIn("Directional interpretation (authoritative):", prompt)
        self.assertIn("Phoenix Coyotes received from Atlanta Thrashers: Draft pick (round 2)", prompt)
        self.assertIn("Atlanta Thrashers received from Phoenix Coyotes: Draft pick (round 1)", prompt)
        self.assertIn("The verdict headline must match this direction", prompt)

    def test_contradictory_short_end_headline_is_flipped(self) -> None:
        session = SimpleNamespace()
        coyotes = SimpleNamespace(full_display_name=lambda: "Phoenix Coyotes")
        thrashers = SimpleNamespace(full_display_name=lambda: "Atlanta Thrashers")
        payload = {
            "verdict": "Thrashers Get the Short End of the Stick!",
            "opinion": "Voros and the defense duo have the potential to contribute more than Podkonicky alone.",
            "suggestions": [],
        }

        with patch(
            "app.services.trade_ai_opinion._asset_labels",
            side_effect=[["Aaron Voros", "Brett Angel", "Jakub Grof"], ["Andrej Podkonicky"]],
        ):
            out = _guard_hypothetical_trade_consistency(
                session,
                payload,
                from_team=coyotes,
                to_team=thrashers,
                left=["player:1", "player:2", "player:3"],
                right=["player:4"],
            )

        self.assertEqual(out["verdict"], "Phoenix Coyotes get the short end of the stick")
        self.assertTrue(out["consistency_guard"])


if __name__ == "__main__":
    unittest.main()
