"""Playoff bracket Discord auto-update payloads and delivery."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services import discord_events
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS
from app.services.playoff_discord_bracket import (
    build_playoff_bracket_discord_payload,
    maybe_enqueue_playoff_bracket_discord,
    record_playoff_bracket_discord_ack,
    series_pair_key_str,
)
from app.services.playoff_discord_predictions import collect_bracket_series
from scripts.league_discord_bot.client import LeagueDiscordBot
from scripts.league_discord_bot.config import BotSettings
from scripts.league_discord_bot.formatters import format_playoff_bracket_deliveries


class PlayoffDiscordBracketTest(unittest.TestCase):
    def test_playoff_bracket_route_defaults(self) -> None:
        self.assertIn("playoff_bracket_update", DEFAULT_EVENT_KEYS)
        self.assertEqual(
            DEFAULT_EVENT_CHANNEL_KEY["playoff_bracket_update"], "playoff-bracket"
        )
        self.assertIn(
            "playoff_bracket_update", discord_events.REPEATABLE_DISCORD_EVENT_KEYS
        )

    def test_series_pair_key_str(self) -> None:
        key = series_pair_key_str(
            {"team_a": {"id": 12}, "team_b": {"id": 5}}
        )
        self.assertEqual(key, "5-12")

    def test_build_payload_without_request_context(self) -> None:
        from app import create_app
        from app.config import make_league_config
        from app.league_db import db

        app = create_app(make_league_config("bowl-cap"))
        with app.app_context():
            result = build_playoff_bracket_discord_payload(
                db.session, db.session, league_slug="bowl-cap"
            )
        if result.get("error"):
            self.skipTest(result["error"])
        payload = result["payload"]
        self.assertEqual(payload["league_slug"], "bowl-cap")
        self.assertGreater(int(payload.get("series_count") or 0), 0)
        first = (payload.get("series") or [])[0]
        self.assertIn("pair_key", first)
        self.assertIn("status_line", first)

    def test_enqueue_skips_when_route_not_configured(self) -> None:
        with patch(
            "app.services.playoff_discord_bracket.is_discord_event_route_active",
            return_value=False,
        ), patch(
            "app.services.playoff_discord_bracket.build_playoff_bracket_discord_payload"
        ) as build_payload:
            queued = maybe_enqueue_playoff_bracket_discord(
                MagicMock(), MagicMock(), "bowl-cap"
            )
        self.assertFalse(queued)
        build_payload.assert_not_called()

    def test_enqueue_skips_when_fingerprint_unchanged(self) -> None:
        bot_cfg = MagicMock()
        bot_cfg.is_enabled = True
        bot_cfg.playoff_bracket_fingerprint = "same-fp"
        payload = {
            "title": "Playoff bracket",
            "league_slug": "bowl-cap",
            "season_id": 1,
            "bracket_fingerprint": "same-fp",
            "series": [{"pair_key": "1-2"}],
            "series_count": 1,
        }
        with patch(
            "app.services.playoff_discord_bracket.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.playoff_discord_bracket.build_playoff_bracket_discord_payload",
            return_value={"payload": payload},
        ), patch(
            "app.services.playoff_discord_bracket.get_league_bot_config",
            return_value=bot_cfg,
        ), patch(
            "app.services.playoff_discord_bracket.enqueue_repeatable_discord_event"
        ) as enqueue:
            queued = maybe_enqueue_playoff_bracket_discord(
                MagicMock(), MagicMock(), "bowl-cap"
            )
        self.assertFalse(queued)
        enqueue.assert_not_called()

    def test_record_ack_upserts_series_posts(self) -> None:
        session = MagicMock()
        bot_cfg = MagicMock()
        stored = MagicMock()
        session.scalar.return_value = stored
        payload = {
            "league_slug": "bowl-cap",
            "season_id": 9,
            "bracket_fingerprint": "fp-abc",
            "series": [{"pair_key": "1-2", "content_hash": "deadbeef"}],
        }
        with patch(
            "app.services.playoff_discord_bracket.get_league_bot_config",
            return_value=bot_cfg,
        ):
            record_playoff_bracket_discord_ack(
                session,
                event_key="playoff_bracket_update",
                payload=payload,
                series_deliveries=[
                    {"pair_key": "1-2", "discord_message_id": "999888777666555444"}
                ],
            )
        self.assertEqual(stored.discord_message_id, "999888777666555444")
        self.assertEqual(bot_cfg.playoff_bracket_fingerprint, "fp-abc")

    def test_formatter_emits_one_delivery_per_series(self) -> None:
        deliveries = format_playoff_bracket_deliveries(
            {
                "league_slug": "bowl-cap",
                "payload": {
                    "title": "Playoff bracket — 2025-26",
                    "url": "https://www.bowlhockey.com/bowl-cap/playoffs",
                    "series": [
                        {
                            "pair_key": "1-2",
                            "round_label": "First round",
                            "series_index": 1,
                            "team_a": {"abbrev": "MTL", "fhm_team_id": 0},
                            "team_b": {"abbrev": "TOR", "fhm_team_id": 3},
                            "series_score": "2-1",
                            "status_line": "Series: **2-1** · MTL leads",
                        },
                        {
                            "pair_key": "3-4",
                            "round_label": "First round",
                            "series_index": 2,
                            "team_a": {"abbrev": "BOS", "fhm_team_id": 1},
                            "team_b": {"abbrev": "NYR", "fhm_team_id": 2},
                            "series_score": "1-1",
                            "status_line": "Series: **1-1** · tied",
                        },
                    ],
                },
            }
        )
        self.assertEqual(len(deliveries), 2)
        self.assertIn("Playoff bracket", deliveries[0]["content"])
        self.assertNotIn("Playoff bracket", deliveries[1]["content"])
        self.assertEqual(deliveries[0]["pair_key"], "1-2")
        self.assertIn("MTL", deliveries[0]["content"])

    def test_deliver_playoff_bracket_patches_each_series(self) -> None:
        bot = LeagueDiscordBot(
            BotSettings(
                token="token",
                shared_secret="secret",
                poll_seconds=8.0,
                delivery_delay_seconds=0.0,
                max_message_parts=2,
                site_timeout_seconds=90.0,
                bot_name="test-bot",
                bot_version="1.0.0",
                league_base_urls={"bowl-cap": "https://example.com/bowl-cap"},
            )
        )
        site_client = MagicMock()
        discord_client = MagicMock()
        event = {
            "id": 42,
            "event_key": "playoff_bracket_update",
            "league_slug": "bowl-cap",
            "discord_channel_id": "111",
            "payload": {
                "title": "Playoff bracket",
                "series": [
                    {
                        "pair_key": "1-2",
                        "edit_message_id": "100",
                        "round_label": "First round",
                        "series_index": 1,
                        "team_a": {"abbrev": "MTL"},
                        "team_b": {"abbrev": "TOR"},
                        "series_score": "2-1",
                        "status_line": "Series: **2-1** · MTL leads",
                    },
                    {
                        "pair_key": "3-4",
                        "round_label": "First round",
                        "series_index": 2,
                        "team_a": {"abbrev": "BOS"},
                        "team_b": {"abbrev": "NYR"},
                        "series_score": "1-0",
                        "status_line": "Series: **1-0** · BOS leads",
                    },
                ],
            },
        }
        with patch.object(
            bot, "patch_discord", side_effect=["200", "201"]
        ) as patch_discord, patch.object(
            bot, "post_discord"
        ) as post_discord, patch.object(
            bot, "ack"
        ) as ack:
            bot.deliver_one(site_client, discord_client, "bowl-cap", event)

        self.assertEqual(patch_discord.call_count, 1)
        post_discord.assert_called_once()
        ack.assert_called_once()
        kwargs = ack.call_args.kwargs
        self.assertEqual(len(kwargs["series_deliveries"]), 2)
        self.assertEqual(kwargs["series_deliveries"][0]["pair_key"], "1-2")


if __name__ == "__main__":
    unittest.main()
