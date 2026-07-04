"""Sim cycle closed-board Discord payload tests."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.sim_cycle_discord import (
    build_export_team_lists,
    build_sim_cycle_discord_payload,
    handle_sim_cycle_after_admin_export,
    publish_closed_sim_cycle_from_admin_export,
    record_sim_cycle_discord_ack,
    sim_log_route_ready,
)
from scripts.league_discord_bot.formatters import format_discord_messages
from scripts.league_discord_bot.team_maps import CAP_TEAMS


class SimCycleClosedBoardTests(unittest.TestCase):
    def test_build_payload_is_always_closed(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            export_date=date(2026, 7, 3),
            discord_message_id=None,
            discord_channel_id=None,
        )
        with patch(
            "app.services.sim_cycle_discord._closed_exported_fhm_team_ids",
            return_value={17},
        ), patch(
            "app.services.sim_cycle_discord.build_export_team_lists",
            return_value={"exported": [17], "pending": [3]},
        ):
            payload = build_sim_cycle_discord_payload(
                site_session, league_session, state
            )
        self.assertEqual(payload["phase"], "closed")
        self.assertIn("closed", payload["title"].lower())
        self.assertNotIn("finalize_on_ack", payload)
        self.assertEqual(payload["embed_color"], 0xB91C1C)

    def test_publish_closed_queues_when_route_ready(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="idle",
            export_date=None,
            finalize_on_ack=False,
            discord_payload_hash=None,
        )
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch(
            "app.services.sim_cycle_discord.resolve_sim_cycle_discord_message_id",
            return_value=None,
        ), patch(
            "app.services.sim_cycle_discord.maybe_enqueue_sim_cycle_discord",
            return_value=True,
        ) as enqueue_mock:
            ok = publish_closed_sim_cycle_from_admin_export(
                site_session,
                league_session,
                "bowl-cap",
                date(2026, 7, 3),
            )
        self.assertTrue(ok)
        self.assertEqual(state.phase, "closed")
        self.assertEqual(state.export_date, date(2026, 7, 3))
        enqueue_mock.assert_called_once()

    def test_handle_export_returns_closed(self) -> None:
        with patch(
            "app.services.sim_cycle_discord.publish_closed_sim_cycle_from_admin_export",
            return_value=True,
        ):
            action = handle_sim_cycle_after_admin_export(
                MagicMock(),
                MagicMock(),
                "bowl-cap",
                date(2026, 7, 3),
            )
        self.assertEqual(action, "closed")

    def test_record_ack_stores_message_id_only(self) -> None:
        session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            discord_message_id=None,
            discord_channel_id=None,
            discord_payload_hash=None,
            finalize_on_ack=False,
            updated_at=None,
        )
        session.scalar.return_value = state
        record_sim_cycle_discord_ack(
            session,
            event_key="sim_cycle_update",
            payload={"source_id": "bowl-cap", "content_hash": "abc"},
            discord_message_id="123456789012345678",
            discord_channel_id="987654321098765432",
        )
        self.assertEqual(state.discord_message_id, "123456789012345678")
        self.assertEqual(state.discord_payload_hash, "abc")

    def test_sim_log_route_ready_delegates(self) -> None:
        site_session = MagicMock()
        with patch(
            "app.services.discord_events.is_discord_event_route_active",
            return_value=True,
        ):
            self.assertTrue(sim_log_route_ready(site_session, "bowl-cap"))


class SimCycleEmbedColorTests(unittest.TestCase):
    def test_league_embed_colors(self) -> None:
        from scripts.league_discord_bot.team_maps import sim_cycle_embed_color

        self.assertEqual(sim_cycle_embed_color("bowl-historical"), 0x166534)
        self.assertEqual(sim_cycle_embed_color("bowl-cap"), 0xB91C1C)
        self.assertEqual(sim_cycle_embed_color("bowl-fantasy"), 0x005DA6)


class SimCycleFormatterTests(unittest.TestCase):
    def test_fail_emote_does_not_collide_with_cap_mtl(self) -> None:
        from scripts.league_discord_bot.team_maps import export_status_emoji

        with patch.dict(
            "scripts.league_discord_bot.team_maps.EXPORT_STATUS_EMOJIS",
            {"success": "<:export_ok:1333588537664213113>", "fail": "<:export_fail:1333588537664213113>"},
        ):
            self.assertEqual(export_status_emoji(success=False, league_slug="bowl-cap"), "")
            self.assertEqual(export_status_emoji(success=True, league_slug="bowl-cap"), "")

    def test_formatter_builds_export_rows_and_footer(self) -> None:
        event = {
            "league_slug": "bowl-cap",
            "event_key": "sim_cycle_update",
            "payload": {
                "title": "Current Sim Cycle (closed)",
                "phase": "closed",
                "exported": [17],
                "pending": [3],
                "exported_count": 1,
                "total_teams": 2,
                "last_updated_at": "2026-07-03T04:01:00+00:00",
                "embed_color": 0xB91C1C,
            },
        }
        bodies = format_discord_messages(event, max_parts=1)
        self.assertEqual(len(bodies), 1)
        embed = bodies[0]["embeds"][0]
        description = embed["description"]
        self.assertNotIn("Atlantic", description)
        self.assertIn("Closed", embed["footer"]["text"])
        self.assertIn("1/2 exported", embed["footer"]["text"])
        self.assertIn("Jul 03 00:01", embed["footer"]["text"])

    def test_formatter_two_export_rows_no_division_labels(self) -> None:
        from scripts.league_discord_bot.team_maps import export_status_emoji

        event = {
            "league_slug": "bowl-cap",
            "event_key": "sim_cycle_update",
            "payload": {
                "title": "Current Sim Cycle (closed)",
                "phase": "closed",
                "exported": [17, 3],
                "pending": [5, 8],
                "exported_count": 2,
                "total_teams": 4,
                "last_updated_at": "2026-07-03T04:01:00+00:00",
                "embed_color": 0xB91C1C,
            },
        }
        bodies = format_discord_messages(event, max_parts=1)
        description = bodies[0]["embeds"][0]["description"]
        lines = description.split("\n")
        success_em = export_status_emoji(success=True, league_slug="bowl-cap") or "✅"
        fail_em = export_status_emoji(success=False, league_slug="bowl-cap") or "❌"
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(success_em))
        self.assertTrue(lines[1].startswith(fail_em))
        self.assertNotIn("Atlantic", description)
        self.assertNotIn("Metropolitan", description)

    def test_formatter_accepts_legacy_divisions_payload(self) -> None:
        from scripts.league_discord_bot.team_maps import export_status_emoji

        event = {
            "league_slug": "bowl-cap",
            "event_key": "sim_cycle_update",
            "payload": {
                "title": "Current Sim Cycle (closed)",
                "phase": "closed",
                "divisions": [
                    {"name": "Atlantic", "exported": [17], "pending": [3]},
                ],
                "exported_count": 1,
                "total_teams": 2,
            },
        }
        bodies = format_discord_messages(event, max_parts=1)
        description = bodies[0]["embeds"][0]["description"]
        success_em = export_status_emoji(success=True, league_slug="bowl-cap") or "✅"
        fail_em = export_status_emoji(success=False, league_slug="bowl-cap") or "❌"
        self.assertNotIn("Atlantic", description)
        self.assertIn(success_em, description)
        self.assertIn(fail_em, description)

    def test_build_export_team_lists_structure(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        team = SimpleNamespace(id=1, fhm_team_id=17, division="")
        with patch(
            "app.services.sim_cycle_discord._sim_cycle_teams_for_league",
            return_value=[team],
        ), patch(
            "app.services.sim_cycle_discord._memberships_by_team_id",
            return_value={},
        ), patch(
            "app.services.sim_cycle_discord._fhm_team_id_for_team",
            return_value=17,
        ):
            lists = build_export_team_lists(
                site_session,
                league_session,
                "bowl-cap",
                {17},
            )
        self.assertEqual(lists["exported"], [17])
        self.assertEqual(lists["pending"], [])


if __name__ == "__main__":
    unittest.main()
