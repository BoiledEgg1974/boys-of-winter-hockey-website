"""Expansion draft picks are commissioner-only (matches entry Draft Hub default)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import app.services.expansion_draft_state as expansion_draft_state


class ExpansionDraftAdminOnlyTest(unittest.TestCase):
    def test_gm_self_picks_always_blocked(self) -> None:
        session = MagicMock()
        draft = MagicMock(
            id=1,
            status="live",
            awaiting_admin_resolution=False,
            expansion_pick_cooldown_active=False,
            league_slug="bowl-cap",
            current_slot_index=0,
        )
        slot = MagicMock(
            overall_pick=1,
            team_id=10,
            round=1,
            phase="goalie",
            forfeited=False,
        )
        player = MagicMock(id=123)

        session.get.return_value = player

        with (
            patch.object(expansion_draft_state, "slots_ordered", return_value=[slot]),
            patch.object(expansion_draft_state, "_pick_row_for_overall", return_value=None),
            patch.object(expansion_draft_state, "validate_pick", return_value=None),
        ):
            msg = expansion_draft_state.record_pick(
                session,
                draft,
                player_id=123,
                user_id=456,
                source="gm",
            )

        self.assertEqual(msg, expansion_draft_state.GM_SELF_PICK_DISABLED_MSG)


if __name__ == "__main__":
    unittest.main()
