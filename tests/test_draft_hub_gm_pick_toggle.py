"""GM self-pick suspension rules for the Draft Hub."""

from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()

