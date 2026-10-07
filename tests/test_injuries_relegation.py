"""Injury classification and Discord delta helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import discord_injury_report_channel_id
from app.services.injuries import classify_injury_status
from app.services.injury_discord import diff_injury_snapshots, maybe_enqueue_injury_report_delta
from scripts.league_discord_bot.formatters import format_discord_messages


class InjuryStatusTests(unittest.TestCase):
    def test_short_max_days_is_day_to_day(self) -> None:
        self.assertEqual(classify_injury_status(5, 3, 5), "day_to_day")

    def test_long_recovery_is_out(self) -> None:
        self.assertEqual(classify_injury_status(200, 90, 160), "out")

    def test_diff_detects_add_and_remove(self) -> None:
        prev = [{"player_id": 1, "team_id": 2, "injury_name": "Concussion", "recovery_days": 10}]
        cur = [{"player_id": 2, "team_id": 3, "injury_name": "Sprain", "recovery_days": 7}]
        delta = diff_injury_snapshots(prev, cur)
        self.assertEqual(len(delta["added"]), 1)
        self.assertEqual(len(delta["removed"]), 1)
        self.assertEqual(delta["added"][0]["player_id"], 2)


class InjuryDiscordFormatterTest(unittest.TestCase):
    def test_active_list_renders_players(self) -> None:
        parts = format_discord_messages(
            {
                "event_key": "injury_report_delta",
                "league_slug": "bowl-fantasy",
                "payload": {
                    "title": "BLUP / BLOW injury report",
                    "active": [
                        {
                            "player_name": "Patrik Laine",
                            "team_abbr": "MTL",
                            "injury_name": "Knee sprain",
                            "status_label": "Day-to-day",
                            "recovery_days": 5,
                        }
                    ],
                },
            },
            max_parts=1,
        )
        content = str(parts[0].get("content") or "")
        self.assertIn("Patrik Laine (MTL)", content)
        self.assertIn("Knee sprain", content)

    def test_empty_active_renders_none(self) -> None:
        parts = format_discord_messages(
            {
                "event_key": "injury_report_delta",
                "league_slug": "bowl-fantasy",
                "payload": {"title": "BLUP / BLOW injury report", "active": []},
            },
            max_parts=1,
        )
        content = str(parts[0].get("content") or "")
        self.assertIn("None", content)


class InjuryDiscordEnqueueTest(unittest.TestCase):
    def test_first_import_with_no_injuries_posts_none(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        with patch(
            "app.services.injury_discord._snapshot_rows",
            return_value=[],
        ), patch(
            "app.services.discord_events.enqueue_discord_event",
            return_value=object(),
        ) as enqueue:
            ok = maybe_enqueue_injury_report_delta(session, "bowl-fantasy")
        self.assertTrue(ok)
        enqueue.assert_called_once()
        payload = enqueue.call_args.kwargs["payload"]
        self.assertEqual(payload["active"], [])
        self.assertEqual(payload["total_active"], 0)

    def test_unchanged_snapshot_skips_enqueue(self) -> None:
        import json

        from app.site_models import InjuryImportSnapshot

        snap = InjuryImportSnapshot(
            league_slug="bowl-fantasy",
            snapshot_json=json.dumps([{"player_id": 1, "injury_name": "X"}]),
        )
        session = MagicMock()
        session.scalar.return_value = snap
        with patch(
            "app.services.injury_discord._snapshot_rows",
            return_value=[{"player_id": 1, "injury_name": "X"}],
        ), patch(
            "app.services.discord_events.enqueue_discord_event",
        ) as enqueue:
            ok = maybe_enqueue_injury_report_delta(session, "bowl-fantasy")
        self.assertFalse(ok)
        enqueue.assert_not_called()


class InjuryDiscordChannelConfigTest(unittest.TestCase):
    def test_default_relegation_injury_channel(self) -> None:
        self.assertEqual(
            discord_injury_report_channel_id("bowl-fantasy"),
            "1209648773307957258",
        )


if __name__ == "__main__":
    unittest.main()
