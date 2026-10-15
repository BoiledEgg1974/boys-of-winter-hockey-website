"""Expansion draft straight vs serpentine slot order."""

from __future__ import annotations

import unittest
from unittest import mock

import app.services.expansion_draft_state as expansion_draft_state
from app.services.expansion_draft_state import (
    EXPANSION_ORDER_SERPENTINE,
    EXPANSION_ORDER_STRAIGHT,
    apply_live_expansion_order,
    phase_pick_order,
    reassign_unpicked_expansion_slots,
    regenerate_slots,
    round_team_order,
)


class _Slot:
    def __init__(self, overall: int, rnd: int, phase: str, team_id: int, *, forfeited: bool = False) -> None:
        self.overall_pick = overall
        self.round = rnd
        self.phase = phase
        self.team_id = team_id
        self.forfeited = forfeited


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

    def test_regenerate_slots_serpentine_resets_between_phases(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="setup",
            goalie_rounds=2,
            skater_rounds=1,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=12,
            phase_order_format=EXPANSION_ORDER_SERPENTINE,
            serpentine_continuous=False,
            expansion_team_order_json="[5, 12]",
        )
        session = mock.MagicMock()
        added: list = []

        def capture_add(obj: object) -> None:
            added.append(obj)

        session.add.side_effect = capture_add
        err = regenerate_slots(session, draft)
        self.assertIsNone(err)
        teams = [(s.phase, s.round, s.team_id) for s in added]
        self.assertEqual(
            teams,
            [
                ("goalie", 1, 5),
                ("goalie", 1, 12),
                ("goalie", 2, 12),
                ("goalie", 2, 5),
                ("skater", 1, 12),
                ("skater", 1, 5),
            ],
        )

    def test_regenerate_slots_serpentine_continuous_into_skater(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="setup",
            goalie_rounds=2,
            skater_rounds=1,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=12,
            phase_order_format=EXPANSION_ORDER_SERPENTINE,
            serpentine_continuous=True,
            expansion_team_order_json="[5, 12]",
        )
        session = mock.MagicMock()
        added: list = []

        def capture_add(obj: object) -> None:
            added.append(obj)

        session.add.side_effect = capture_add
        err = regenerate_slots(session, draft)
        self.assertIsNone(err)
        teams = [(s.phase, s.round, s.team_id) for s in added]
        self.assertEqual(
            teams,
            [
                ("goalie", 1, 5),
                ("goalie", 1, 12),
                ("goalie", 2, 12),
                ("goalie", 2, 5),
                ("skater", 1, 5),
                ("skater", 1, 12),
            ],
        )

    def test_apply_live_order_rejects_setup_draft(self) -> None:
        draft = mock.MagicMock(status="setup", expansion_team_order_json="[5, 12]")
        changed, err = apply_live_expansion_order(
            mock.MagicMock(),
            draft,
            order_fmt=EXPANSION_ORDER_STRAIGHT,
            serp_cont=False,
            goalie_first=12,
            skater_first=5,
        )
        self.assertEqual(changed, 0)
        self.assertIn("live", err or "")

    def test_apply_live_order_rewrites_remaining_and_locks_picks(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="live",
            goalie_rounds=1,
            skater_rounds=1,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=5,
            phase_order_format=EXPANSION_ORDER_STRAIGHT,
            serpentine_continuous=False,
            expansion_team_order_json="[5, 12]",
            current_slot_index=1,
        )
        slots = [
            _Slot(1, 1, "goalie", 5),
            _Slot(2, 1, "goalie", 12),
            _Slot(3, 1, "skater", 5),
            _Slot(4, 1, "skater", 12),
        ]
        session = mock.MagicMock()
        with (
            mock.patch.object(expansion_draft_state, "slots_ordered", return_value=slots),
            mock.patch.object(expansion_draft_state, "picked_overalls", return_value={1}),
            mock.patch.object(expansion_draft_state, "sync_current_slot_and_clock") as sync_clock,
            mock.patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            changed, err = apply_live_expansion_order(
                session,
                draft,
                order_fmt=EXPANSION_ORDER_STRAIGHT,
                serp_cont=False,
                goalie_first=12,
                skater_first=12,
            )
        self.assertIsNone(err)
        self.assertEqual(changed, 3)
        self.assertEqual([s.team_id for s in slots], [5, 5, 12, 5])
        self.assertEqual(draft.goalie_phase_first_team_id, 12)
        self.assertEqual(draft.skater_phase_first_team_id, 12)
        sync_clock.assert_called_once()

    def test_apply_live_order_mismatch_does_not_keep_settings(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="live",
            goalie_rounds=2,
            skater_rounds=1,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=5,
            phase_order_format=EXPANSION_ORDER_STRAIGHT,
            serpentine_continuous=False,
            expansion_team_order_json="[5, 12]",
            current_slot_index=0,
        )
        slots = [_Slot(1, 1, "goalie", 5), _Slot(2, 1, "goalie", 12)]
        with (
            mock.patch.object(expansion_draft_state, "slots_ordered", return_value=slots),
            mock.patch.object(expansion_draft_state, "picked_overalls", return_value=set()),
        ):
            changed, err = apply_live_expansion_order(
                mock.MagicMock(),
                draft,
                order_fmt=EXPANSION_ORDER_SERPENTINE,
                serp_cont=False,
                goalie_first=12,
                skater_first=12,
            )
        self.assertEqual(changed, 0)
        self.assertIn("slot list", err or "")
        self.assertEqual(draft.phase_order_format, EXPANSION_ORDER_STRAIGHT)
        self.assertEqual(draft.goalie_phase_first_team_id, 5)
        self.assertEqual([s.team_id for s in slots], [5, 12])

    def test_apply_live_order_resets_clock_when_current_team_changes(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="live",
            goalie_rounds=1,
            skater_rounds=0,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=5,
            phase_order_format=EXPANSION_ORDER_STRAIGHT,
            serpentine_continuous=False,
            expansion_team_order_json="[5, 12]",
            current_slot_index=0,
            pick_started_at="set",
        )
        slots = [_Slot(1, 1, "goalie", 5), _Slot(2, 1, "goalie", 12)]
        with (
            mock.patch.object(expansion_draft_state, "slots_ordered", return_value=slots),
            mock.patch.object(expansion_draft_state, "picked_overalls", return_value=set()),
            mock.patch.object(expansion_draft_state, "sync_current_slot_and_clock") as sync_clock,
            mock.patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            changed, err = apply_live_expansion_order(
                mock.MagicMock(),
                draft,
                order_fmt=EXPANSION_ORDER_STRAIGHT,
                serp_cont=False,
                goalie_first=12,
                skater_first=12,
            )
        self.assertIsNone(err)
        self.assertEqual(changed, 2)
        self.assertEqual([s.team_id for s in slots], [12, 5])
        self.assertIsNone(draft.pick_started_at)
        sync_clock.assert_called_once()

    def test_reassign_unpicked_slots_locks_picked_and_skipped(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="live",
            expansion_team_order_json="[5, 12]",
            current_slot_index=2,
        )
        slots = [
            _Slot(1, 1, "goalie", 5),
            _Slot(2, 1, "goalie", 12, forfeited=True),
            _Slot(3, 1, "skater", 5),
        ]
        with (
            mock.patch.object(expansion_draft_state, "slots_ordered", return_value=slots),
            mock.patch.object(expansion_draft_state, "picked_overalls", return_value={1}),
            mock.patch.object(expansion_draft_state, "sync_current_slot_and_clock") as sync_clock,
            mock.patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            changed, err = reassign_unpicked_expansion_slots(
                mock.MagicMock(),
                draft,
                {1: 12, 2: 5, 3: 12},
            )
        self.assertIsNone(err)
        self.assertEqual(changed, 1)
        self.assertEqual(slots[0].team_id, 5)
        self.assertEqual(slots[1].team_id, 12)
        self.assertEqual(slots[2].team_id, 12)
        sync_clock.assert_called_once()

    def test_reassign_unpicked_slots_rejects_non_franchise_team(self) -> None:
        draft = mock.MagicMock(
            id=1,
            status="live",
            expansion_team_order_json="[5, 12]",
            current_slot_index=0,
        )
        slots = [_Slot(1, 1, "goalie", 5), _Slot(2, 1, "goalie", 12)]
        with (
            mock.patch.object(expansion_draft_state, "slots_ordered", return_value=slots),
            mock.patch.object(expansion_draft_state, "picked_overalls", return_value=set()),
            mock.patch.object(expansion_draft_state, "sync_current_slot_and_clock"),
            mock.patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            changed, err = reassign_unpicked_expansion_slots(
                mock.MagicMock(),
                draft,
                {1: 99},
            )
        self.assertEqual(changed, 0)
        self.assertIn("expansion franchises", err or "")
        self.assertEqual(slots[0].team_id, 5)


if __name__ == "__main__":
    unittest.main()
