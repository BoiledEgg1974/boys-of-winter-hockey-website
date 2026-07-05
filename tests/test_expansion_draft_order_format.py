"""Expansion draft straight vs serpentine slot order."""

from __future__ import annotations

import unittest

from app.services.expansion_draft_state import (
    EXPANSION_ORDER_SERPENTINE,
    EXPANSION_ORDER_STRAIGHT,
    phase_pick_order,
    round_team_order,
)


class ExpansionDraftOrderFormatTest(unittest.TestCase):
    def test_phase_pick_order_rotates_first_team(self) -> None:
        base = [10, 20, 30]
        self.assertEqual(phase_pick_order(base, 20), [20, 30, 10])

    def test_straight_repeats_each_round(self) -> None:
        order = [1, 2, 3]
        self.assertEqual(round_team_order(order, 1, EXPANSION_ORDER_STRAIGHT), [1, 2, 3])
        self.assertEqual(round_team_order(order, 2, EXPANSION_ORDER_STRAIGHT), [1, 2, 3])
        self.assertEqual(round_team_order(order, 3, EXPANSION_ORDER_STRAIGHT), [1, 2, 3])

    def test_serpentine_reverses_even_rounds(self) -> None:
        order = [1, 2, 3]
        self.assertEqual(round_team_order(order, 1, EXPANSION_ORDER_SERPENTINE), [1, 2, 3])
        self.assertEqual(round_team_order(order, 2, EXPANSION_ORDER_SERPENTINE), [3, 2, 1])
        self.assertEqual(round_team_order(order, 3, EXPANSION_ORDER_SERPENTINE), [1, 2, 3])

    def test_serpentine_two_teams(self) -> None:
        order = [5, 12]
        self.assertEqual(round_team_order(order, 1, EXPANSION_ORDER_SERPENTINE), [5, 12])
        self.assertEqual(round_team_order(order, 2, EXPANSION_ORDER_SERPENTINE), [12, 5])


if __name__ == "__main__":
    unittest.main()
