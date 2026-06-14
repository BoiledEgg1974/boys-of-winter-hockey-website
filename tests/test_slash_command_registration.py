"""Slash command registration script helpers."""
from __future__ import annotations

import unittest

from scripts.league_discord_bot.register_slash_commands import _parse_guild_ids


class SlashCommandRegistrationTest(unittest.TestCase):
    def test_parse_guild_ids_accepts_multiple_env_values(self) -> None:
        self.assertEqual(
            _parse_guild_ids(
                "111111111111111111, 222222222222222222",
                "222222222222222222;333333333333333333",
            ),
            [
                "111111111111111111",
                "222222222222222222",
                "333333333333333333",
            ],
        )


if __name__ == "__main__":
    unittest.main()
