"""Expansion draft Discord route normalization for all leagues."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.discord_events import (
    EXPANSION_DRAFT_DISCORD_EVENT_KEYS,
    _normalize_expansion_draft_discord_routes,
)


class DiscordExpansionMigrationTest(unittest.TestCase):
    def test_normalizes_legacy_channel_key_and_backfills_sibling_ids(self) -> None:
        pick_made = SimpleNamespace(
            event_key="expansion_draft_pick_made",
            channel_key="expansion-draft-discussion",
            discord_channel_id="111111111111111111",
            label="",
            updated_at=None,
        )
        on_clock = SimpleNamespace(
            event_key="expansion_draft_on_clock",
            channel_key="expansion-draft",
            discord_channel_id="",
            label="",
            updated_at=None,
        )
        command_pick = SimpleNamespace(
            event_key="expansion_draft_command_pick",
            channel_key="expansion-draft-pick",
            discord_channel_id="",
            label="",
            updated_at=None,
        )
        rows = [pick_made, on_clock, command_pick]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        changed = _normalize_expansion_draft_discord_routes(session, "bowl-fantasy")

        self.assertTrue(changed)
        self.assertEqual(pick_made.channel_key, "expansion-draft")
        self.assertEqual(on_clock.discord_channel_id, "111111111111111111")
        self.assertEqual(command_pick.discord_channel_id, "")

    def test_all_expansion_event_keys_are_covered(self) -> None:
        self.assertEqual(
            EXPANSION_DRAFT_DISCORD_EVENT_KEYS,
            {
                "expansion_draft_pick_made",
                "expansion_draft_on_clock",
                "expansion_draft_completed",
                "expansion_draft_command_list",
                "expansion_draft_command_pick",
            },
        )

    def test_no_change_when_routes_already_normalized(self) -> None:
        row = SimpleNamespace(
            event_key="expansion_draft_pick_made",
            channel_key="expansion-draft",
            discord_channel_id="222222222222222222",
            label="Expansion draft pick (live)",
            updated_at=None,
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = [row]
        self.assertFalse(_normalize_expansion_draft_discord_routes(session, "bowl-cap"))


if __name__ == "__main__":
    unittest.main()
