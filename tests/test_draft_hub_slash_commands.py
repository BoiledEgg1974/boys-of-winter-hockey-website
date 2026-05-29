"""Draft Hub slash command registration and helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import discord_interactions
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_LABELS, DEFAULT_EVENT_KEYS


class DraftHubSlashCommandsTest(unittest.TestCase):
    def test_draft_and_list_commands_are_registered(self) -> None:
        names = {cmd["name"] for cmd in discord_interactions.COMMAND_DEFINITIONS}
        self.assertIn("draft", names)
        self.assertIn("list", names)
        draft_cmd = next(cmd for cmd in discord_interactions.COMMAND_DEFINITIONS if cmd["name"] == "draft")
        self.assertEqual(draft_cmd["options"][0]["name"], "player")
        self.assertTrue(draft_cmd["options"][0]["required"])

    def test_command_routes_are_seeded_defaults(self) -> None:
        self.assertIn("draft_hub_command_pick", DEFAULT_EVENT_KEYS)
        self.assertIn("draft_hub_command_list", DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["draft_hub_command_pick"], "draft-pick")
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["draft_hub_command_list"], "draft-list")
        self.assertIn("Draft Hub /draft", DEFAULT_EVENT_LABELS["draft_hub_command_pick"])

    def test_resolve_draft_player_detects_ambiguous_names(self) -> None:
        players = [
            SimpleNamespace(id=1, fhm_player_id="101", full_name="Alex Smith", overall_potential=17.0),
            SimpleNamespace(id=2, fhm_player_id="102", full_name="Alex Stone", overall_potential=16.0),
        ]
        player, err = discord_interactions._resolve_draft_player("Alex", players)
        self.assertIsNone(player)
        self.assertIn("Multiple players matched", err or "")
        self.assertIn("id `1`", err or "")

    def test_resolve_draft_player_accepts_site_id(self) -> None:
        players = [
            SimpleNamespace(id=1, fhm_player_id="101", full_name="Alex Smith", overall_potential=17.0),
            SimpleNamespace(id=2, fhm_player_id="102", full_name="Alex Stone", overall_potential=16.0),
        ]
        player, err = discord_interactions._resolve_draft_player("2", players)
        self.assertIsNone(err)
        self.assertEqual(player.full_name, "Alex Stone")

    def test_list_command_formats_top_20(self) -> None:
        draft = SimpleNamespace(name="2026 Draft Hub", status="live")
        players = [
            SimpleNamespace(
                id=i,
                fhm_player_id=str(1000 + i),
                full_name=f"Player {i}",
                overall_potential=20 - i / 10,
                overall_ability=10 + i / 10,
                position="C",
            )
            for i in range(1, 25)
        ]
        with (
            patch.object(discord_interactions, "_channel_check", return_value=None),
            patch.object(discord_interactions, "_eligible_remaining_players", return_value=(draft, players)),
        ):
            resp = discord_interactions._handle_draft_list_command({"channel_id": "1"}, "bowl-fantasy")
        content = resp["data"]["content"]
        self.assertIn("top 20", content)
        self.assertIn("Player 1", content)
        self.assertIn("Player 20", content)
        self.assertNotIn("Player 21", content)


if __name__ == "__main__":
    unittest.main()
