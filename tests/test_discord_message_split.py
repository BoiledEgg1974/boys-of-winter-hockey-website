"""Discord outbound message splitting for league_discord_bot."""
from __future__ import annotations

import unittest

from scripts.league_discord_bot.formatters import (
    DISCORD_SITE_MORE_FOOTER,
    _split_message_bodies,
    format_discord_messages,
)


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

    def test_text_only_news_posts_full_body_without_embed(self):
        body = "Paragraph one.\n\n" + ("Word " * 120)
        parts = format_discord_messages(
            {
                "league_slug": "bowl-historical",
                "event_key": "gm_news_published",
                "payload": {
                    "title": "Series tied",
                    "body": body,
                    "has_image": False,
                    "team_abbrev": "TOR",
                },
            },
            max_parts=4,
        )
        self.assertGreaterEqual(len(parts), 1)
        self.assertNotIn("embeds", parts[0])
        joined = "\n".join(p.get("content", "") for p in parts)
        self.assertIn("Series tied", joined)
        self.assertIn("Paragraph one.", joined)
        self.assertIn(DISCORD_SITE_MORE_FOOTER, parts[-1].get("content", ""))

    def test_draft_pick_is_text_only_without_embed(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "draft_hub_pick_made",
                "payload": {
                    "draft_name": "2026 Draft",
                    "round": 1,
                    "overall_pick": 3,
                    "player_name": "Connor Bedard",
                    "player_pos": "C",
                    "pick_source": "gm",
                    "body": "Round 1 · Overall #3 · Connor Bedard (C) · gm",
                    "has_image": False,
                    "team_abbrev": "CHI",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertNotIn("embeds", parts[0])
        content = parts[0].get("content", "")
        self.assertIn("Connor Bedard", content)
        self.assertIn(DISCORD_SITE_MORE_FOOTER, content)

    def test_trade_request_is_text_only_without_embed(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "trade_request",
                "payload": {
                    "request_id": 12,
                    "request_type": "trade",
                    "status": "approved",
                    "title": "Trade approved",
                    "body": "Leafs send prospect for pick.",
                    "has_image": False,
                    "team_abbrev": "TOR",
                },
            },
            max_parts=2,
        )
        self.assertNotIn("embeds", parts[0])
        self.assertIn("Trade approved", parts[0].get("content", ""))

    def test_news_with_image_keeps_embed_link(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-historical",
                "event_key": "admin_news_published",
                "payload": {
                    "title": "Photo post",
                    "body": "Full story text.",
                    "body_preview": "Full stor…",
                    "has_image": True,
                    "url": "https://www.bowlhockey.com/bowl-historical/league-headlines#a1",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertIn("embeds", parts[0])
        self.assertEqual(
            parts[0]["embeds"][0]["url"],
            "https://www.bowlhockey.com/bowl-historical/league-headlines#a1",
        )


if __name__ == "__main__":
    unittest.main()
