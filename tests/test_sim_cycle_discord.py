"""Sim cycle tracker parser and Discord payload tests."""
from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.sim_cycle_discord import (
    build_division_export_groups,
    build_sim_cycle_discord_payload,
    handle_sim_cycle_after_admin_export,
    maybe_auto_start_sim_cycle,
    record_sim_cycle_discord_ack,
    restart_sim_cycle_after_close_ack,
)
from app.services.sim_cycle_tracker_parser import parse_export_fhm_team_ids_from_messages
from scripts.league_discord_bot.formatters import format_discord_messages
from scripts.league_discord_bot.team_maps import CAP_TEAMS


class SimCycleTrackerParserTests(unittest.TestCase):
    def test_parses_team_emote_from_webhook_message(self) -> None:
        _tid, (_abbr, emoji) = next(iter(CAP_TEAMS.items()))
        messages = [
            {
                "id": "999",
                "timestamp": datetime.utcnow().isoformat(),
                "content": f"Export complete {emoji}",
                "author": {"id": "111", "bot": True},
            }
        ]
        ids, latest = parse_export_fhm_team_ids_from_messages(
            "bowl-cap",
            messages,
            allowed_author_ids={"111"},
        )
        self.assertIn(int(_tid), ids)
        self.assertEqual(latest, "999")

    def test_parses_abbrev_from_embed_description(self) -> None:
        messages = [
            {
                "id": "1000",
                "timestamp": datetime.utcnow().isoformat(),
                "content": "",
                "embeds": [{"description": "BUF exported successfully"}],
                "author": {"id": "222", "bot": True},
            }
        ]
        ids, _latest = parse_export_fhm_team_ids_from_messages("bowl-cap", messages)
        buf_id = next(tid for tid, (abbr, _em) in CAP_TEAMS.items() if abbr == "BUF")
        self.assertIn(buf_id, ids)

    def test_ignores_non_bot_messages(self) -> None:
        messages = [
            {
                "id": "1001",
                "timestamp": datetime.utcnow().isoformat(),
                "content": "BUF",
                "author": {"id": "333", "bot": False},
            }
        ]
        ids, _latest = parse_export_fhm_team_ids_from_messages("bowl-cap", messages)
        self.assertEqual(ids, set())


class SimCycleDiscordPayloadTests(unittest.TestCase):
    def _mock_team(self, *, tid: int, fhm_id: int, div_id: int, conf_id: int = 0) -> SimpleNamespace:
        team = SimpleNamespace(
            id=tid,
            fhm_team_id=str(fhm_id),
            fhm_division_id=div_id,
            fhm_conference_id=conf_id,
            abbreviation="X",
        )
        team.full_display_name = lambda: f"Team {tid}"
        return team

    def test_build_division_export_groups_splits_exported_and_pending(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        mem1 = SimpleNamespace(team_id=1, fhm_team_id=17, status="active", user_id=1)
        mem2 = SimpleNamespace(team_id=2, fhm_team_id=3, status="active", user_id=2)
        site_session.scalars.return_value.all.return_value = [mem1, mem2]
        team1 = self._mock_team(tid=1, fhm_id=17, div_id=0, conf_id=0)
        team2 = self._mock_team(tid=2, fhm_id=3, div_id=1, conf_id=0)
        league_session.scalars.return_value.all.return_value = [team1, team2]

        with patch(
            "app.services.sim_cycle_discord._division_maps_for_league",
            return_value=({(0, 0): "Northeast Division", (0, 1): "Atlantic Division"}, {}),
        ):
            groups = build_division_export_groups(
                site_session, league_session, "bowl-cap", {17}
            )
        by_name = {g["name"]: g for g in groups}
        self.assertIn("Northeast Division", by_name)
        self.assertEqual(by_name["Northeast Division"]["exported"], [17])
        self.assertEqual(by_name["Atlantic Division"]["pending"], [3])

    def test_payload_includes_edit_message_id_when_stored(self) -> None:
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            export_date=date(2026, 7, 3),
            live_exported_fhm_team_ids_json="[]",
        )
        site_session = MagicMock()
        league_session = MagicMock()
        site_session.scalars.return_value.all.return_value = []
        league_session.scalars.return_value.all.return_value = []

        with patch(
            "app.services.sim_cycle_discord.resolve_sim_cycle_discord_message_id",
            return_value="123456789012345678",
        ), patch(
            "app.services.sim_cycle_discord.build_division_export_groups",
            return_value=[],
        ):
            payload = build_sim_cycle_discord_payload(
                site_session, league_session, state, post_new_message=False
            )
        self.assertEqual(payload.get("edit_message_id"), "123456789012345678")
        self.assertNotIn("post_new_message", payload)

    def test_payload_omits_edit_on_new_post(self) -> None:
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            export_date=date(2026, 7, 3),
            live_exported_fhm_team_ids_json="[]",
        )
        site_session = MagicMock()
        league_session = MagicMock()

        with patch(
            "app.services.sim_cycle_discord.build_division_export_groups",
            return_value=[],
        ):
            payload = build_sim_cycle_discord_payload(
                site_session, league_session, state, post_new_message=True
            )
        self.assertTrue(payload.get("post_new_message"))
        self.assertNotIn("edit_message_id", payload)

    def test_record_ack_restarts_live_cycle_on_finalize(self) -> None:
        session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            finalize_on_ack=True,
            discord_message_id="99",
            discord_channel_id="88",
            discord_payload_hash="abc",
            tracker_last_message_id="77",
            live_exported_fhm_team_ids_json="[1]",
            export_date=date(2026, 7, 3),
            cycle_started_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.scalar.return_value = state
        with patch(
            "app.services.sim_cycle_discord.restart_sim_cycle_after_close_ack"
        ) as restart_mock:
            restart_mock.return_value = (state, True)
            record_sim_cycle_discord_ack(
                session,
                event_key="sim_cycle_update",
                payload={"source_id": "bowl-cap", "finalize_on_ack": True},
                discord_message_id="123456789012345678",
                discord_channel_id="999",
            )
        restart_mock.assert_called_once_with(session, session, "bowl-cap")

    def test_restart_after_close_starts_fresh_live_cycle(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        with patch(
            "app.services.sim_cycle_discord.reset_sim_cycle_state"
        ) as reset_mock, patch(
            "app.services.sim_cycle_discord.start_sim_cycle"
        ) as start_mock:
            reset_mock.return_value = SimpleNamespace(phase="idle")
            start_mock.return_value = (SimpleNamespace(phase="live"), True)
            state, queued = restart_sim_cycle_after_close_ack(
                site_session, league_session, "bowl-cap"
            )
        reset_mock.assert_called_once_with(site_session, "bowl-cap")
        start_mock.assert_called_once()
        self.assertTrue(queued)
        self.assertEqual(state.phase, "live")


class SimCycleAutomationTests(unittest.TestCase):
    def test_handle_export_closes_live_cycle(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(phase="live", league_slug="bowl-cap")
        with patch(
            "app.services.sim_cycle_discord.sim_cycle_routes_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch(
            "app.services.sim_cycle_discord.close_sim_cycle_from_admin_export",
            return_value=True,
        ) as close_mock:
            action = handle_sim_cycle_after_admin_export(
                site_session,
                league_session,
                "bowl-cap",
                date(2026, 7, 3),
            )
        self.assertEqual(action, "closed")
        close_mock.assert_called_once()

    def test_handle_export_starts_live_when_idle(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(phase="idle", league_slug="bowl-cap")
        with patch(
            "app.services.sim_cycle_discord.sim_cycle_routes_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch(
            "app.services.sim_cycle_discord.start_sim_cycle",
            return_value=(state, True),
        ) as start_mock:
            action = handle_sim_cycle_after_admin_export(
                site_session,
                league_session,
                "bowl-cap",
                date(2026, 7, 3),
            )
        self.assertEqual(action, "started")
        start_mock.assert_called_once()

    def test_sim_log_route_ready_without_tracker(self) -> None:
        site_session = MagicMock()
        with patch(
            "app.services.discord_events.is_discord_event_route_active",
            side_effect=lambda _s, *, league_slug, event_key: event_key == "sim_cycle_update",
        ):
            from app.services.sim_cycle_discord import (
                sim_cycle_routes_ready,
                sim_cycle_tracker_route_ready,
            )

            self.assertTrue(sim_cycle_routes_ready(site_session, "bowl-cap"))
            self.assertFalse(sim_cycle_tracker_route_ready(site_session, "bowl-cap"))

    def test_maybe_auto_start_skips_when_not_idle(self) -> None:
        site_session = MagicMock()
        state = SimpleNamespace(phase="live")
        with patch(
            "app.services.sim_cycle_discord.sim_cycle_routes_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch("app.services.sim_cycle_discord.start_sim_cycle") as start_mock:
            queued = maybe_auto_start_sim_cycle(site_session, site_session, "bowl-cap")
        self.assertFalse(queued)
        start_mock.assert_not_called()


class SimCycleFormatterTests(unittest.TestCase):
    def test_fail_emote_does_not_collide_with_cap_mtl(self) -> None:
        from scripts.league_discord_bot.team_maps import export_status_emoji

        # Placeholder IDs that match MTL must not render as a team logo.
        with patch.dict(
            "scripts.league_discord_bot.team_maps.EXPORT_STATUS_EMOJIS",
            {"success": "<:export_ok:1333588537664213113>", "fail": "<:export_fail:1333588537664213113>"},
        ):
            self.assertEqual(export_status_emoji(success=False, league_slug="bowl-cap"), "")
            self.assertEqual(export_status_emoji(success=True, league_slug="bowl-cap"), "")

    def test_formatter_builds_division_lines_and_footer(self) -> None:
        event = {
            "league_slug": "bowl-cap",
            "event_key": "sim_cycle_update",
            "payload": {
                "title": "Current Sim Cycle (live)",
                "phase": "live",
                "divisions": [
                    {
                        "name": "Atlantic",
                        "exported": [17],
                        "pending": [3],
                    }
                ],
                "exported_count": 1,
                "total_teams": 2,
                "last_updated_at": "2026-07-03T00:01:00",
                "embed_color": 0xF1C40F,
            },
        }
        bodies = format_discord_messages(event, max_parts=1)
        self.assertEqual(len(bodies), 1)
        embed = bodies[0]["embeds"][0]
        self.assertIn("Atlantic", embed["description"])
        self.assertIn("In progress", embed["footer"]["text"])
        self.assertIn("1/2 exported", embed["footer"]["text"])


if __name__ == "__main__":
    unittest.main()
