"""Playoff predictions Discord /predict command and formatter."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services import discord_interactions
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS, DEFAULT_EVENT_LABELS
from app.services.playoff_discord_predictions import collect_bracket_series
from scripts.league_discord_bot.formatters import format_discord_messages


def _admin_payload(*, permissions: str = "8", channel_id: str = "123456789012345678") -> dict:
    return {
        "type": 2,
        "data": {"name": "predict"},
        "channel_id": channel_id,
        "member": {"permissions": permissions, "user": {"id": "999"}},
    }


class PlayoffDiscordPredictionsTest(unittest.TestCase):
    def test_predict_command_registered_for_admins(self) -> None:
        names = {cmd["name"] for cmd in discord_interactions.COMMAND_DEFINITIONS}
        self.assertIn("predict", names)
        predict_cmd = next(cmd for cmd in discord_interactions.COMMAND_DEFINITIONS if cmd["name"] == "predict")
        self.assertEqual(predict_cmd.get("default_member_permissions"), "8")

    def test_playoff_predictions_route_defaults(self) -> None:
        self.assertIn("playoff_predictions", DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY["playoff_predictions"], "playoff-predictions")
        self.assertIn("Playoff predictions", DEFAULT_EVENT_LABELS["playoff_predictions"])

    def test_is_discord_admin(self) -> None:
        self.assertTrue(discord_interactions._is_discord_admin(_admin_payload()))
        self.assertFalse(discord_interactions._is_discord_admin(_admin_payload(permissions="0")))

    def test_predict_denies_non_admin(self) -> None:
        resp = discord_interactions._handle_predict_command(
            _admin_payload(permissions="0"),
            "bowl-cap",
        )
        self.assertIn("administrators only", resp["data"]["content"])

    def test_predict_requires_playoff_predictions_channel(self) -> None:
        with patch.object(discord_interactions, "_channel_check", return_value="Use this command in the configured `playoff-predictions` channel."):
            resp = discord_interactions._handle_predict_command(_admin_payload(), "bowl-cap")
        self.assertIn("configured `playoff-predictions` channel", resp["data"]["content"])

    def test_predict_queues_outbound_event(self) -> None:
        payload = {
            "title": "Playoff predictions — 2025-26",
            "series": [{"round_label": "First round"}],
            "series_count": 1,
            "source_id": "season-1",
        }
        queued = MagicMock()
        with (
            patch.object(discord_interactions, "_channel_check", return_value=None),
            patch.object(discord_interactions, "_site_user_for_discord", return_value=None),
            patch(
                "app.services.playoff_discord_predictions.build_playoff_predictions_discord_payload",
                return_value={"payload": payload},
            ),
            patch("app.services.discord_events.enqueue_discord_event", return_value=queued) as enqueue,
            patch.object(discord_interactions.db.session, "commit") as commit,
            patch.object(discord_interactions.db.session, "rollback"),
        ):
            resp = discord_interactions._handle_predict_command(_admin_payload(), "bowl-cap")
        self.assertIn("Queued playoff predictions", resp["data"]["content"])
        enqueue.assert_called_once()
        commit.assert_called_once()

    def test_collect_bracket_series_dedupes_pairs(self) -> None:
        series = {
            "team_a": {"id": 1, "abbreviation": "MTL"},
            "team_b": {"id": 2, "abbreviation": "TOR"},
            "wins_a": 0,
            "wins_b": 0,
        }
        bracket = {
            "first_round": [series, series],
            "second_round": [series],
            "conference_finals": [],
            "championship": None,
        }
        rows = collect_bracket_series(bracket)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "First round")

    def test_formatter_emits_one_message_per_matchup(self) -> None:
        series_row = {
            "round_label": "First round",
            "team_a": {"abbrev": "MTL", "fhm_team_id": 0},
            "team_b": {"abbrev": "TOR", "fhm_team_id": 3},
            "team_a_stats": "MTL: GF/G 3.2 (2nd), GA/G 2.5 (5th), PP% 22.1% (3rd), PK% 81.2% (4th)",
            "team_b_stats": "TOR: GF/G 3.0 (4th), GA/G 2.7 (6th), PP% 20.0% (8th), PK% 79.0% (9th)",
            "prediction_line": "MTL 58.3% to win series",
            "h2h_line": "MTL 3-2-1 vs TOR · 18-15 goals",
            "series_score": "0-0",
        }
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "playoff_predictions",
                "payload": {
                    "title": "Playoff predictions — 2025-26",
                    "prediction_method_note": "Based on regular-season stats.",
                    "series": [
                        series_row,
                        {
                            **series_row,
                            "team_a": {"abbrev": "BOS", "fhm_team_id": 1},
                            "team_b": {"abbrev": "NYR", "fhm_team_id": 2},
                            "prediction_line": "BOS 52.0% to win series",
                            "h2h_line": "BOS 2-3-0 vs NYR · 14-16 goals",
                        },
                    ],
                },
            },
            max_parts=4,
        )
        self.assertEqual(len(parts), 2)
        self.assertNotIn("embeds", parts[0])
        self.assertNotIn("embeds", parts[1])
        self.assertIn("Playoff predictions", parts[0]["content"])
        self.assertNotIn("Playoff predictions", parts[1]["content"])
        self.assertIn("MTL", parts[0]["content"])
        self.assertIn("TOR", parts[0]["content"])
        self.assertNotIn("BOS", parts[0]["content"])
        self.assertIn("BOS", parts[1]["content"])
        self.assertIn("NYR", parts[1]["content"])
        self.assertIn("Prediction: MTL 58.3% to win series", parts[0]["content"])
        self.assertIn("Prediction: BOS 52.0% to win series", parts[1]["content"])
        self.assertIn("Based on regular-season stats.", parts[1]["content"])
        self.assertIn("bowlhockey.com", parts[1]["content"])

    def test_build_playoff_predictions_payload_without_request_context(self) -> None:
        from app import create_app
        from app.config import make_league_config
        from app.league_db import db
        from app.services.playoff_discord_predictions import build_playoff_predictions_discord_payload

        app = create_app(make_league_config("bowl-cap"))
        with app.app_context():
            result = build_playoff_predictions_discord_payload(
                db.session,
                league_slug="bowl-cap",
            )
        if result.get("error"):
            self.skipTest(result["error"])
        self.assertIn("payload", result)
        self.assertGreater(int(result["payload"].get("series_count") or 0), 0)

    def test_formatter_emits_text_only_playoff_predictions(self) -> None:
        parts = format_discord_messages(
            {
                "league_slug": "bowl-cap",
                "event_key": "playoff_predictions",
                "payload": {
                    "title": "Playoff predictions — 2025-26",
                    "series": [
                        {
                            "round_label": "First round",
                            "team_a": {"abbrev": "MTL", "fhm_team_id": 0},
                            "team_b": {"abbrev": "TOR", "fhm_team_id": 3},
                            "team_a_stats": "MTL: GF/G 3.2 (2nd), GA/G 2.5 (5th), PP% 22.1% (3rd), PK% 81.2% (4th)",
                            "team_b_stats": "TOR: GF/G 3.0 (4th), GA/G 2.7 (6th), PP% 20.0% (8th), PK% 79.0% (9th)",
                            "prediction_line": "MTL 58.3% to win series",
                            "h2h_line": "MTL 3-2-1 vs TOR · 18-15 goals",
                            "series_score": "0-0",
                        }
                    ],
                },
            },
            max_parts=4,
        )
        self.assertEqual(len(parts), 1)
        self.assertNotIn("embeds", parts[0])
        content = parts[0].get("content", "")
        self.assertIn("Playoff predictions", content)
        self.assertIn("MTL", content)
        self.assertIn("TOR", content)
        self.assertIn("Prediction: MTL 58.3% to win series", content)
        self.assertIn("Regular-season H2H", content)
        self.assertIn("PP%", content)


if __name__ == "__main__":
    unittest.main()
