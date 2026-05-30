"""Trade AI prompt construction."""
from __future__ import annotations

import unittest
from datetime import date, datetime

from app.services.trade_ai_opinion import build_logged_trade_prompt_block
from app.services.trade_log import TradeLogRow


class TradeAiOpinionPromptTests(unittest.TestCase):
    def test_manual_sends_blocks_are_rewritten_as_trade_directions(self) -> None:
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

        self.assertIn("Boston Bruins traded away: Bobby Hull; Harry Howell", prompt)
        self.assertIn(
            "Montreal Canadiens received from Boston Bruins: Bobby Hull; Harry Howell",
            prompt,
        )
        self.assertIn(
            "Do not describe an asset as acquired by the same team whose 'sends' block lists it.",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
