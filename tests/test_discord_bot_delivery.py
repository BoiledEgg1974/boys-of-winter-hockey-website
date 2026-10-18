"""Discord bot delivery helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts.league_discord_bot.client import LeagueDiscordBot
from scripts.league_discord_bot.config import BotSettings


def _bot() -> LeagueDiscordBot:
    return LeagueDiscordBot(
        BotSettings(
            token="token",
            shared_secret="secret",
            poll_seconds=8.0,
            tracker_poll_seconds=4.0,
            delivery_delay_seconds=0.0,
            max_message_parts=2,
            site_timeout_seconds=90.0,
            bot_name="test-bot",
            bot_version="1.0.0",
            league_base_urls={"bowl-historical": "https://example.com/bowl-historical"},
        )
    )


class DiscordBotDeliveryTest(unittest.TestCase):
    def test_deliver_one_falls_back_to_post_when_patch_unknown_message(self) -> None:
        bot = _bot()
        site_client = MagicMock()
        discord_client = MagicMock()
        event = {
            "id": 99,
            "discord_channel_id": "111",
            "event_key": "bowl_six_leaders_update",
            "league_slug": "bowl-historical",
            "payload": {
                "edit_message_id": "999",
                "title": "BOWL Six leaders",
                "body": "Week stats",
            },
        }
        with patch.object(
            bot, "discord_message_exists", return_value=True
        ), patch.object(
            bot,
            "patch_discord",
            side_effect=RuntimeError('Discord API 404: {"code": 10008, "message": "Unknown Message"}'),
        ) as patch_discord, patch.object(
            bot, "post_discord", return_value="222"
        ) as post_discord, patch.object(
            bot, "ack"
        ) as ack, patch(
            "scripts.league_discord_bot.client.format_discord_messages",
            return_value=[{"embeds": [{"title": "BOWL Six leaders"}]}],
        ):
            bot.deliver_one(site_client, discord_client, "bowl-historical", event)

        patch_discord.assert_called_once()
        post_discord.assert_called_once()
        ack.assert_called_once_with(
            site_client,
            "bowl-historical",
            99,
            discord_message_id="222",
            discord_channel_id="111",
        )

    def test_deliver_one_posts_new_when_edit_target_missing(self) -> None:
        bot = _bot()
        site_client = MagicMock()
        discord_client = MagicMock()
        event = {
            "id": 100,
            "discord_channel_id": "111",
            "event_key": "bowl_six_leaders_update",
            "league_slug": "bowl-historical",
            "payload": {
                "edit_message_id": "1513566264436719767",
                "title": "BOWL Six leaders",
                "body": "Week stats",
            },
        }
        with patch.object(bot, "discord_message_exists", return_value=False), patch.object(
            bot, "patch_discord"
        ) as patch_discord, patch.object(
            bot, "post_discord", return_value="333"
        ) as post_discord, patch.object(
            bot, "ack"
        ) as ack, patch(
            "scripts.league_discord_bot.client.format_discord_messages",
            return_value=[{"content": "**BOWL Six leaders**", "embeds": [{"title": "BOWL Six leaders"}]}],
        ):
            bot.deliver_one(site_client, discord_client, "bowl-historical", event)

        patch_discord.assert_not_called()
        post_discord.assert_called_once()
        ack.assert_called_once_with(
            site_client,
            "bowl-historical",
            100,
            discord_message_id="333",
            discord_channel_id="111",
        )

    def test_deliver_one_skips_edit_when_post_new_message(self) -> None:
        bot = _bot()
        site_client = MagicMock()
        discord_client = MagicMock()
        event = {
            "id": 101,
            "discord_channel_id": "111",
            "event_key": "bowl_six_leaders_update",
            "league_slug": "bowl-historical",
            "payload": {
                "post_new_message": True,
                "edit_message_id": "1513566264436719767",
                "title": "BOWL Six leaders",
                "body": "Week stats",
            },
        }
        with patch.object(
            bot, "discord_message_exists"
        ) as exists, patch.object(
            bot, "patch_discord"
        ) as patch_discord, patch.object(
            bot, "post_discord", return_value="444"
        ) as post_discord, patch.object(
            bot, "ack"
        ) as ack, patch(
            "scripts.league_discord_bot.client.format_discord_messages",
            return_value=[{"content": "**BOWL Six leaders**", "embeds": [{"title": "BOWL Six leaders"}]}],
        ):
            bot.deliver_one(site_client, discord_client, "bowl-historical", event)

        exists.assert_not_called()
        patch_discord.assert_not_called()
        post_discord.assert_called_once()
        ack.assert_called_once()

    def test_run_cycle_heartbeats_when_pending_times_out(self) -> None:
        bot = _bot()
        site_client = MagicMock()
        discord_client = MagicMock()
        with patch.object(bot, "poll_pending", side_effect=TimeoutError("timed out")), patch.object(
            bot, "heartbeat"
        ) as heartbeat:
            bot.run_cycle(site_client, discord_client)
        slugs = [c.kwargs["league_slug"] for c in heartbeat.call_args_list]
        self.assertIn("bowl-historical", slugs)
        self.assertTrue(
            any("timed out" in str(c.kwargs.get("last_error") or "") for c in heartbeat.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
