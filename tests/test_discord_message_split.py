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

    def test_text_only_news_includes_team_gm_mention(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "gm_news_published",
                "payload": {
                    "title": "Team update",
                    "body": "A team-tagged story.",
                    "has_image": False,
                    "team_abbrev": "TOR",
                    "team_gm_mention": "<@123456789012345678>",
                },
            },
            max_parts=2,
        )
        self.assertIn("<@123456789012345678>", parts[0].get("content", ""))

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

    def test_draft_pick_includes_team_gm_mention(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "draft_hub_pick_made",
                "payload": {
                    "draft_name": "2026 Draft",
                    "round": 1,
                    "overall_pick": 3,
                    "player_name": "Connor Bedard",
                    "has_image": False,
                    "team_abbrev": "CHI",
                    "team_gm_mention": "<@123456789012345678>",
                },
            },
            max_parts=2,
        )

        self.assertIn("<@123456789012345678>", parts[0].get("content", ""))

    def test_draft_hub_on_clock_is_text_only_with_bold_gm_mention_and_url(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "draft_hub_on_clock",
                "payload": {
                    "draft_name": "2026 Draft Hub",
                    "team_name": "Toronto Towers",
                    "team_abbrev": "TOR",
                    "round": 1,
                    "selection": 3,
                    "gm_mentions": "<@123456789012345678>",
                    "url": "https://www.bowlhockey.com/bowl-fantasy/draft-hub",
                    "has_image": False,
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        content = parts[0].get("content", "")
        self.assertNotIn("embeds", parts[0])
        self.assertIn("On the clock:", content)
        self.assertIn("Round 1, Selection 3", content)
        self.assertIn("**<@123456789012345678>, make your selection with /draft when ready.**", content)
        self.assertIn("https://www.bowlhockey.com/bowl-fantasy/draft-hub", content)
        self.assertIn(DISCORD_SITE_MORE_FOOTER, content)

    def test_draft_hub_on_deck_is_text_only_with_mention_and_url(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "draft_hub_on_deck",
                "payload": {
                    "draft_name": "2026 Draft Hub",
                    "team_name": "Hamilton Steel",
                    "team_abbrev": "HAM",
                    "round": 1,
                    "selection": 4,
                    "gm_mentions": "<@222222222222222222>",
                    "url": "https://www.bowlhockey.com/bowl-fantasy/draft-hub",
                    "has_image": False,
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        content = parts[0].get("content", "")
        self.assertNotIn("embeds", parts[0])
        self.assertIn("On deck:", content)
        self.assertIn("Round 1, Selection 4", content)
        self.assertIn("<@222222222222222222>, get ready!", content)
        self.assertIn("https://www.bowlhockey.com/bowl-fantasy/draft-hub", content)

    def test_draft_hub_completed_is_text_only_with_archive_link(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "draft_hub_completed",
                "payload": {
                    "draft_name": "2026 Draft Hub",
                    "pick_count": 3,
                    "recap_lines": [
                        "#1 · Toronto Towers: Player One (C)",
                        "#2 · Hamilton Steel: Player Two (G)",
                    ],
                    "archive_url": "https://www.bowlhockey.com/bowl-fantasy/draft-hub/archive/7",
                    "has_image": False,
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        content = parts[0].get("content", "")
        self.assertNotIn("embeds", parts[0])
        self.assertIn("**2026 Draft Hub complete**", content)
        self.assertIn("3 pick(s) recorded.", content)
        self.assertIn("#1 · Toronto Towers: Player One (C)", content)
        self.assertIn("Archive: https://www.bowlhockey.com/bowl-fantasy/draft-hub/archive/7", content)

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

    def test_trade_request_includes_team_gm_mention(self):
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
                    "team_gm_mention": "<@123456789012345678>",
                },
            },
            max_parts=2,
        )

        self.assertIn("<@123456789012345678>", parts[0].get("content", ""))

    def test_staff_transaction_uses_gm_display_name_not_email(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "staff_transaction_posted",
                "payload": {
                    "action": "hired",
                    "staff_name": "Coach Example",
                    "role_label": "Head Coach",
                    "gm_name": "ARCHIE5",
                    "gm_email": "archie@example.invalid",
                    "body": "Coach Example (Head Coach)\nGM: ARCHIE5",
                    "has_image": False,
                    "team_abbrev": "TOR",
                },
            },
            max_parts=2,
        )
        content = parts[0].get("content", "")
        self.assertIn("GM: ARCHIE5", content)
        self.assertNotIn("archie@example.invalid", content)

    def test_staff_transaction_includes_gm_mention_when_available(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "staff_transaction_posted",
                "payload": {
                    "action": "hired",
                    "staff_name": "Coach Example",
                    "role_label": "Scout",
                    "gm_name": "ARCHIE5",
                    "body": "Coach Example (Scout)\nGM: ARCHIE5",
                    "has_image": False,
                    "team_abbrev": "TOR",
                    "team_gm_mention": "<@123456789012345678>",
                },
            },
            max_parts=2,
        )

        content = parts[0].get("content", "")
        self.assertIn("<@123456789012345678>", content)
        self.assertIn("GM: ARCHIE5", content)

    def test_staff_transaction_does_not_fall_back_to_gm_email(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "staff_transaction_posted",
                "payload": {
                    "action": "fired",
                    "staff_name": "Coach Example",
                    "role_label": "Assistant Coach",
                    "gm_email": "archie@example.invalid",
                    "body": "Coach Example (Assistant Coach)",
                    "has_image": False,
                    "team_abbrev": "TOR",
                },
            },
            max_parts=2,
        )
        content = parts[0].get("content", "")
        self.assertNotIn("archie@example.invalid", content)
        self.assertNotIn("GM:", content)

    def test_ap_redemption_uses_gm_display_name_not_email(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "ap_redemption_posted",
                "payload": {
                    "title": "AP redemption approved",
                    "body": "Redemption approved: Gold boost",
                    "has_image": False,
                    "team_abbrev": "TOR",
                    "gm_name": "ARCHIE5",
                    "gm_email": "archie@example.invalid",
                    "redemption_label": "Gold boost",
                    "total_cost": 50,
                },
            },
            max_parts=2,
        )
        content = parts[0].get("content", "")
        self.assertIn("GM: ARCHIE5", content)
        self.assertNotIn("archie@example.invalid", content)

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
        self.assertEqual(parts[0]["content"], "**BOWL Six leaders — Week of 1969-03-10**")
        self.assertIn("embeds", parts[0])
        self.assertEqual(
            parts[0]["embeds"][0]["url"],
            "https://www.bowlhockey.com/bowl-historical/league-headlines#a1",
        )

    def test_news_with_image_puts_team_gm_mention_in_content(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-historical",
                "event_key": "gm_news_published",
                "payload": {
                    "title": "Photo post",
                    "body": "Full story text.",
                    "has_image": True,
                    "url": "https://www.bowlhockey.com/bowl-historical/league-headlines#a1",
                    "team_gm_mention": "<@222222222222222222>",
                },
            },
            max_parts=2,
        )
        self.assertEqual(parts[0].get("content"), "<@222222222222222222>")
        self.assertIn("embeds", parts[0])

    def test_news_with_image_does_not_duplicate_content_and_embed_body(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "admin_news_published",
                "payload": {
                    "title": "C Suzuki to Stick Around Trois-Rivieres Under New Deal",
                    "body": "Cole Suzuki said his new contract came together quite easily.",
                    "body_preview": "Cole Suzuki said his new contract came together quite easily.",
                    "has_image": True,
                    "url": "https://www.bowlhockey.com/bowl-fantasy/league-headlines#a1",
                    "team_abbrev": "TRL",
                    "team_name": "Trois-Rivières Lions",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertNotIn("content", parts[0])
        embed = parts[0]["embeds"][0]
        self.assertIn("Cole Suzuki", embed["description"])
        self.assertIn("TRL", embed["author"]["name"])

    def test_trade_market_selling_is_embed_only(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "trade_market_selling_posted",
                "payload": {
                    "title": "Trade Market — selling update",
                    "body": "Now selling:\n• [Player X](https://www.bowlhockey.com/bowl-cap/player/99) — ask $2M · wants Prospects",
                    "url": "https://www.bowlhockey.com/bowl-cap/trade-market",
                    "team_abbrev": "TOR",
                    "team_name": "Toronto Towers",
                    "team_logo_url": "https://www.bowlhockey.com/bowl-cap/static/logos/teams/tor.png",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertNotIn("content", parts[0])
        embed = parts[0]["embeds"][0]
        self.assertEqual(embed["url"], "https://www.bowlhockey.com/bowl-cap/trade-market")
        self.assertIn("[Player X](https://www.bowlhockey.com/bowl-cap/player/99)", embed["description"])
        self.assertEqual(embed["author"]["name"], "Toronto Towers (TOR)")
        self.assertEqual(
            embed["thumbnail"]["url"],
            "https://www.bowlhockey.com/bowl-cap/static/logos/teams/tor.png",
        )

    def test_trade_market_buying_is_embed_only(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "trade_market_buying_posted",
                "payload": {
                    "title": "Trade Market — buying interests",
                    "body": "Looking to acquire:\n• Draft Picks\n• Prospects",
                    "url": "https://www.bowlhockey.com/bowl-fantasy/trade-market",
                    "team_abbrev": "HAL",
                    "team_name": "Halifax Privateers",
                    "team_logo_url": "https://www.bowlhockey.com/bowl-fantasy/static/logos/teams/halifax.png",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertNotIn("content", parts[0])
        embed = parts[0]["embeds"][0]
        self.assertIn("Looking to acquire", embed["description"])
        self.assertEqual(embed["author"]["name"], "Halifax Privateers (HAL)")

    def test_trade_market_embed_includes_team_gm_mention_content(self):
        parts = format_discord_messages(
            {
                "league_slug": "bowl-fantasy",
                "event_key": "trade_market_buying_posted",
                "payload": {
                    "title": "Trade Market — buying interests",
                    "body": "Looking to acquire:\n• Draft Picks",
                    "url": "https://www.bowlhockey.com/bowl-fantasy/trade-market",
                    "team_abbrev": "HAL",
                    "team_name": "Halifax Privateers",
                    "team_gm_mention": "<@123456789012345678>",
                },
            },
            max_parts=2,
        )

        self.assertEqual(parts[0].get("content"), "<@123456789012345678>")
        self.assertIn("embeds", parts[0])

    def test_bowl_six_leaders_are_embed_only(self):
        body = "Week: Week of 1969-03-10\nSlate status: locked\n\nTop performers\n1. Andre Lacroix — 19.5 pts"
        parts = format_discord_messages(
            {
                "league_slug": "bowl-historical",
                "event_key": "bowl_six_leaders_update",
                "payload": {
                    "title": "BOWL Six leaders — Week of 1969-03-10",
                    "body": body,
                    "body_preview": body[:40],
                    "url": "https://www.bowlhockey.com/bowl-historical/bowl-six",
                },
            },
            max_parts=2,
        )
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["content"], "**BOWL Six leaders — Week of 1969-03-10**")
        self.assertEqual(parts[0]["embeds"][0]["description"], body)
        self.assertEqual(
            parts[0]["embeds"][0]["url"],
            "https://www.bowlhockey.com/bowl-historical/bowl-six",
        )


if __name__ == "__main__":
    unittest.main()
