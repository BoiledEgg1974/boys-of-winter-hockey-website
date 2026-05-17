"""Discord outbound message splitting for league_discord_bot."""
from __future__ import annotations

import unittest

from scripts.league_discord_bot.formatters import _split_message_bodies, format_discord_messages


class DiscordMessageSplitTest(unittest.TestCase):
    def test_short_message_single_part(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-historical",
                "event_key": "announcement_posted",
                "payload": {"title": "Hello", "body": "Short note."},
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertIn("Hello", parts[0].get("content", ""))

    def test_long_content_splits_into_two(self):
        parts = _split_message_bodies({"content": "word " * 500}, max_parts=2)
        self.assertEqual(len(parts), 2)
        self.assertLessEqual(len(parts[0].get("content", "")), 2000)
        self.assertLessEqual(len(parts[1].get("content", "")), 2000)


if __name__ == "__main__":
    unittest.main()
