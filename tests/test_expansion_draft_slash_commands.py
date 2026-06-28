"""Expansion Draft slash command registration and helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import discord_interactions
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_LABELS, DEFAULT_EVENT_KEYS


class ExpansionDraftSlashCommandsTest(unittest.TestCase):
    def test_expansion_commands_are_registered(self) -> None:
        names = {cmd["name"] for cmd in discord_interactions.COMMAND_DEFINITIONS}
        self.assertIn("expansionstatus", names)
        self.assertIn("expansionpick", names)
        self.assertIn("expansionlist", names)
        pick_cmd = next(cmd for cmd in discord_interactions.COMMAND_DEFINITIONS if cmd["name"] == "expansionpick")
        self.assertEqual(pick_cmd["options"][0]["name"], "player")
        self.assertTrue(pick_cmd["options"][0]["required"])

    def test_command_routes_are_seeded_defaults(self) -> None:
        self.assertIn("expansion_draft_command_pick", DEFAULT_EVENT_KEYS)
        self.assertIn("expansion_draft_command_list", DEFAULT_EVENT_KEYS)
        self.assertIn("expansion_draft_on_clock", DEFAULT_EVENT_KEYS)
        self.assertIn("expansion_draft_completed", DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["expansion_draft_command_pick"], "expansion-draft-pick")
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["expansion_draft_command_list"], "expansion-draft")
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["expansion_draft_pick_made"], "expansion-draft")
        self.assertIn("Expansion draft /expansionpick", DEFAULT_EVENT_LABELS["expansion_draft_command_pick"])

    def test_list_command_formats_top_20_with_phase(self) -> None:
        draft = SimpleNamespace(name="2026 Expansion Draft", status="live")
        players = [
            SimpleNamespace(
                id=i,
                fhm_player_id=str(1000 + i),
                full_name=f"Player {i}",
                overall_potential=20 - i / 10,
                overall_ability=10 + i / 10,
                position="G",
            )
            for i in range(1, 25)
        ]
        with (
            patch.object(discord_interactions, "_channel_check", return_value=None),
            patch.object(
                discord_interactions,
                "_expansion_eligible_remaining_players",
                return_value=(draft, players, "Goalie"),
            ),
        ):
            resp = discord_interactions._handle_expansion_list_command({"channel_id": "1"}, "bowl-historical")
        content = resp["data"]["content"]
        self.assertIn("Goalie phase", content)
        self.assertIn("top 20", content)
        self.assertIn("Player 1", content)
        self.assertIn("Player 20", content)
        self.assertNotIn("Player 21", content)

    def test_pick_command_requires_league_staff(self) -> None:
        user = SimpleNamespace(id=1, is_authenticated=True)
        draft = SimpleNamespace(name="2026 Expansion Draft", status="live")
        with (
            patch.object(discord_interactions, "_channel_check", return_value=None),
            patch.object(discord_interactions, "_site_user_for_discord", return_value=user),
            patch("app.auth_login.league_hub_staff", return_value=False),
            patch.object(
                discord_interactions,
                "_expansion_eligible_remaining_players",
                return_value=(draft, [], None),
            ),
        ):
            resp = discord_interactions._handle_expansion_pick_command({"channel_id": "1"}, "bowl-historical")
        self.assertIn("league staff", resp["data"]["content"])


if __name__ == "__main__":
    unittest.main()
