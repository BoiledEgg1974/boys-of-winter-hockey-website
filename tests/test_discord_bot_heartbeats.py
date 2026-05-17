"""Discord bot heartbeat helpers for unified league_discord_bot worker."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.discord_events import canonical_discord_bot_name


class DiscordBotHeartbeatTest(unittest.TestCase):
    def test_canonical_discord_bot_name_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISCORD_BOT_NAME", None)
            self.assertEqual(canonical_discord_bot_name(), "league-discord-bot")

    def test_canonical_discord_bot_name_from_env(self):
        with patch.dict(os.environ, {"DISCORD_BOT_NAME": "league-discord-bot"}):
            self.assertEqual(canonical_discord_bot_name(), "league-discord-bot")


if __name__ == "__main__":
    unittest.main()
