"""Playoff bracket Discord auto-update payloads and delivery."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services import discord_events
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS
from app.services.playoff_discord_bracket import (
    build_playoff_bracket_discord_payload,
    enqueue_fresh_playoff_bracket_discord,
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

    def test_build_payload_uses_site_round_and_season_labels(self) -> None:
        """Historical opening round lives in second_round slots; label it like the site."""
        season = MagicMock()
        season.id = 7
        season.label = "1967-68"  # stale stored label
        season.start_year = 1969
        season.end_year = 1970
        with patch(
            "app.services.playoff_discord_bracket.get_current_season",
            return_value=season,
        ), patch(
            "app.services.playoff_discord_bracket.playoff_bracket_payload",
            return_value={
                "empty": False,
                "projection_only": False,
                "second_round": [
                    {
                        "team_a": {"id": 10, "abbrev": "PHI"},
                        "team_b": {"id": 9, "abbrev": "OAK"},
                        "wins_a": 0,
                        "wins_b": 2,
                    }
                ],
            },
        ), patch(
            "app.services.playoff_discord_bracket.collect_bracket_series",
            return_value=[
                (
                    "Second round",
                    {
                        "team_a": {"id": 10, "abbrev": "PHI"},
                        "team_b": {"id": 9, "abbrev": "OAK"},
                        "wins_a": 0,
                        "wins_b": 2,
                    },
                )
            ],
        ), patch(
            "app.services.playoff_discord_bracket._load_series_posts",
            return_value={},
        ), patch(
            "app.services.playoff_discord_bracket.playoff_bracket_cache_fingerprint",
            return_value="fp",
        ), patch(
            "app.services.playoff_discord_bracket.build_league_public_url",
            return_value="/playoffs",
        ):
            result = build_playoff_bracket_discord_payload(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-historical",
            )
        payload = result["payload"]
        self.assertEqual(payload["title"], "Playoff bracket — 1969-70")
        self.assertEqual(payload["season_label"], "1969-70")
        self.assertEqual(payload["series"][0]["round_label"], "Division Semi-Finals")

    def test_build_payload_skips_projection_only_bracket(self) -> None:
        season = MagicMock()
        season.id = 42
        season.label = "1967-68"
        with patch(
            "app.services.playoff_discord_bracket.get_current_season",
            return_value=season,
        ), patch(
            "app.services.playoff_discord_bracket.playoff_bracket_payload",
            return_value={
                "empty": False,
                "projection_only": True,
                "message": "Projected from current regular-season standings.",
                "second_round": [{"team_a": {"id": 1}, "team_b": {"id": 2}}],
            },
        ):
            result = build_playoff_bracket_discord_payload(
                MagicMock(), MagicMock(), league_slug="bowl-historical"
            )
        self.assertEqual(result["error"], "Playoffs have not started yet.")

    def test_enqueue_skips_when_bracket_is_projection_only(self) -> None:
        with patch(
            "app.services.playoff_discord_bracket.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.playoff_discord_bracket.build_playoff_bracket_discord_payload",
            return_value={"error": "Playoffs have not started yet."},
        ), patch(
            "app.services.playoff_discord_bracket.enqueue_repeatable_discord_event"
        ) as enqueue:
            queued = maybe_enqueue_playoff_bracket_discord(
                MagicMock(), MagicMock(), "bowl-historical"
            )
        self.assertFalse(queued)
        enqueue.assert_not_called()

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

    def test_formatter_skips_projection_only_payload(self) -> None:
        deliveries = format_playoff_bracket_deliveries(
            {
                "league_slug": "bowl-historical",
                "payload": {
                    "title": "Playoff bracket — 1967-68",
                    "projection_note": "Projected from current regular-season standings.",
                    "series": [
                        {
                            "pair_key": "1-2",
                            "round_label": "Second round",
                            "series_index": 1,
                            "team_a": {"abbrev": "MTL"},
                            "team_b": {"abbrev": "NYR"},
                            "series_score": "0-0",
                            "status_line": "Series: **0-0** · tied",
                        },
                    ],
                },
            }
        )
        self.assertEqual(deliveries, [])

    def test_deliver_playoff_bracket_patches_each_series(self) -> None:
        bot = LeagueDiscordBot(
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
            bot, "discord_message_exists", return_value=True
        ), patch.object(
            bot, "ack"
        ) as ack:
            bot.deliver_one(site_client, discord_client, "bowl-cap", event)

        self.assertEqual(patch_discord.call_count, 1)
        post_discord.assert_called_once()
        ack.assert_called_once()
        kwargs = ack.call_args.kwargs
        self.assertEqual(len(kwargs["series_deliveries"]), 2)
        self.assertEqual(kwargs["series_deliveries"][0]["pair_key"], "1-2")

    def test_deliver_playoff_bracket_raises_when_projection_only(self) -> None:
        bot = LeagueDiscordBot(
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
        event = {
            "id": 99,
            "event_key": "playoff_bracket_update",
            "league_slug": "bowl-historical",
            "discord_channel_id": "111",
            "payload": {
                "title": "Playoff bracket — 1967-68",
                "projection_note": "Projected from current regular-season standings.",
                "series": [],
            },
        }
        with self.assertRaises(RuntimeError):
            bot._deliver_playoff_bracket_update(
                MagicMock(), MagicMock(), "bowl-historical", 99, "111", event
            )

    def test_enqueue_fresh_clears_edit_targets_and_sets_post_new_messages(self) -> None:
        season = MagicMock()
        season.id = 9
        payload = {
            "title": "Playoff bracket",
            "league_slug": "bowl-cap",
            "season_id": 9,
            "bracket_fingerprint": "fp-new",
            "series": [{"pair_key": "1-2", "edit_message_id": "100"}],
            "series_count": 1,
            "post_new_messages": True,
        }
        session = MagicMock()
        with patch(
            "app.services.playoff_discord_bracket.get_current_season",
            return_value=season,
        ), patch(
            "app.services.playoff_discord_bracket._clear_playoff_bracket_discord_series_posts"
        ) as clear_posts, patch(
            "app.services.playoff_discord_bracket.maybe_enqueue_playoff_bracket_discord",
            return_value=True,
        ) as enqueue:
            ok = enqueue_fresh_playoff_bracket_discord(session, MagicMock(), "bowl-cap")
        self.assertTrue(ok)
        clear_posts.assert_called_once_with(session, "bowl-cap", season_id=9)
        enqueue.assert_called_once()
        self.assertTrue(enqueue.call_args.kwargs["force_new_post"])

    def test_build_payload_omits_edit_ids_when_post_new_messages(self) -> None:
        season = MagicMock()
        season.id = 42
        season.label = "1999-2000"
        season.start_year = 1999
        season.end_year = 2000
        stored = MagicMock()
        stored.discord_message_id = "999"
        with patch(
            "app.services.playoff_discord_bracket.get_current_season",
            return_value=season,
        ), patch(
            "app.services.playoff_discord_bracket.playoff_bracket_payload",
            return_value={
                "empty": False,
                "projection_only": False,
                "second_round": [
                    {
                        "team_a": {"id": 1, "abbrev": "MTL"},
                        "team_b": {"id": 2, "abbrev": "TOR"},
                        "wins_a": 1,
                        "wins_b": 0,
                    }
                ],
            },
        ), patch(
            "app.services.playoff_discord_bracket.collect_bracket_series",
            return_value=[
                (
                    "First round",
                    {
                        "team_a": {"id": 1, "abbrev": "MTL"},
                        "team_b": {"id": 2, "abbrev": "TOR"},
                        "wins_a": 1,
                        "wins_b": 0,
                    },
                )
            ],
        ), patch(
            "app.services.playoff_discord_bracket._load_series_posts",
            return_value={"1-2": stored},
        ), patch(
            "app.services.playoff_discord_bracket.playoff_bracket_cache_fingerprint",
            return_value="fp",
        ), patch(
            "app.services.playoff_discord_bracket.build_league_public_url",
            return_value="/playoffs",
        ):
            result = build_playoff_bracket_discord_payload(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-cap",
                post_new_messages=True,
            )
        series = result["payload"]["series"]
        self.assertTrue(result["payload"].get("post_new_messages"))
        self.assertIsNone(series[0].get("edit_message_id"))


if __name__ == "__main__":
    unittest.main()
