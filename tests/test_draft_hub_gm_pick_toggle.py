"""GM self-pick suspension rules for the Draft Hub."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import app.services.draft_hub_state as draft_hub_state


class DraftHubGmPickToggleTest(unittest.TestCase):
    def test_gm_self_picks_disabled_blocks_gm_pick(self) -> None:
        session = MagicMock()
        draft = MagicMock(
            id=1,
            status="live",
            awaiting_admin_resolution=False,
            league_slug="bowl-fantasy",
            current_slot_index=0,
            gm_picks_enabled=False,
            timer_seconds=120,
            pick_deadline_at=None,
        )
        slot = MagicMock(
            overall_pick=3,
            team_id=10,
            round=1,
            forfeited=False,
        )

        player_id = 123
        admin_user_id = 999

        with (
            patch.object(draft_hub_state, "slots_ordered", return_value=[slot]),
            patch.object(draft_hub_state, "_pick_row_for_overall", return_value=None),
            patch.object(draft_hub_state, "picked_player_ids", return_value=set()),
            patch.object(draft_hub_state, "draft_eligibility_params", return_value=MagicMock()),
            patch.object(draft_hub_state, "eligible_id_set_for_draft", return_value={player_id}),
        ):
            msg = draft_hub_state.record_pick(
                session,
                draft,
                player_id=player_id,
                user_id=admin_user_id,
                source="gm",
            )

        self.assertIsInstance(msg, str)
        self.assertEqual(
            msg,
            "GM self-picks are disabled for this draft; the commissioner will make selections.",
        )

    def test_expired_legacy_deadline_does_not_block_gm_pick(self) -> None:
        session = MagicMock()
        draft = MagicMock(
            id=1,
            status="live",
            awaiting_admin_resolution=False,
            league_slug="bowl-fantasy",
            current_slot_index=0,
            gm_picks_enabled=True,
            timer_seconds=120,
            pick_deadline_at=datetime.utcnow() - timedelta(minutes=5),
        )
        slot = MagicMock(
            overall_pick=3,
            team_id=10,
            round=1,
            forfeited=False,
        )

        player_id = 123
        gm_user_id = 456

        with (
            patch.object(draft_hub_state, "slots_ordered", return_value=[slot]),
            patch.object(draft_hub_state, "_pick_row_for_overall", return_value=None),
            patch.object(draft_hub_state, "picked_player_ids", return_value=set()),
            patch.object(draft_hub_state, "draft_eligibility_params", return_value=MagicMock()),
            patch.object(draft_hub_state, "eligible_id_set_for_draft", return_value={player_id}),
            patch.object(draft_hub_state, "gm_user_ids_for_team", return_value=[gm_user_id]),
            patch.object(draft_hub_state, "_remove_player_from_all_queues"),
            patch.object(draft_hub_state, "sync_current_slot_and_clock"),
            patch.object(draft_hub_state, "_finalize_draft_if_done"),
        ):
            msg = draft_hub_state.record_pick(
                session,
                draft,
                player_id=player_id,
                user_id=gm_user_id,
                source="gm",
            )

        self.assertIsNone(msg)
        self.assertIsNone(draft.pick_deadline_at)
        self.assertFalse(draft.awaiting_admin_resolution)


if __name__ == "__main__":
    unittest.main()

