"""Sim cycle tracker parser and Discord payload tests."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.sim_cycle_discord import (
    build_export_team_lists,
    build_sim_cycle_discord_payload,
    force_start_live_sim_cycle,
    handle_sim_cycle_after_admin_export,
    maybe_enqueue_sim_cycle_discord,
    publish_closed_sim_cycle_from_admin_export,
    record_sim_cycle_discord_ack,
    restart_sim_cycle_after_close_ack,
    sim_cycle_routes_ready,
    sim_cycle_tracker_route_ready,
    start_sim_cycle,
)
from app.services.sim_cycle_tracker_parser import (
    message_indicates_export,
    parse_export_fhm_team_ids_from_messages,
    tracker_watermark_before_cycle,
)
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

    def test_ignores_exports_before_cycle_anchor(self) -> None:
        anchor = datetime(2026, 7, 3, 18, 0, 0)
        messages = [
            {
                "id": "1003",
                "timestamp": "2026-07-03T14:00:00+00:00",
                "content": "DET has exported!",
                "author": {"id": "111", "bot": True},
            },
            {
                "id": "1004",
                "timestamp": "2026-07-03T19:00:00+00:00",
                "content": "TOR has exported!",
                "author": {"id": "111", "bot": True},
            },
        ]
        ids, _latest = parse_export_fhm_team_ids_from_messages(
            "bowl-historical",
            messages,
            cycle_started_at=anchor,
        )
        self.assertEqual(ids, {3})

    def test_watermark_before_cycle_anchor(self) -> None:
        anchor = datetime(2026, 7, 3, 18, 0, 0)
        messages = [
            {
                "id": "100",
                "timestamp": "2026-07-03T19:00:00+00:00",
                "content": "TOR has exported!",
                "author": {"id": "111", "bot": True},
            },
            {
                "id": "90",
                "timestamp": "2026-07-03T17:00:00+00:00",
                "content": "MTL has exported!",
                "author": {"id": "111", "bot": True},
            },
        ]
        self.assertEqual(
            tracker_watermark_before_cycle(messages, cycle_started_at=anchor),
            "90",
        )
        self.assertTrue(message_indicates_export(messages[0]))


class SimCycleDiscordPayloadTests(unittest.TestCase):
    def test_build_payload_live_phase(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            export_date=date(2026, 7, 3),
            live_exported_fhm_team_ids_json="[17]",
            discord_message_id=None,
            discord_channel_id=None,
        )
        with patch(
            "app.services.sim_cycle_discord.build_export_team_lists",
            return_value={"exported": [17], "pending": [3]},
        ):
            payload = build_sim_cycle_discord_payload(
                site_session, league_session, state
            )
        self.assertEqual(payload["phase"], "live")
        self.assertIn("live", payload["title"].lower())
        self.assertEqual(payload["embed_color"], 0xB91C1C)

    def test_payload_includes_edit_message_id_when_stored(self) -> None:
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            export_date=date(2026, 7, 3),
            live_exported_fhm_team_ids_json="[]",
        )
        site_session = MagicMock()
        league_session = MagicMock()
        with patch(
            "app.services.sim_cycle_discord.resolve_sim_cycle_discord_message_id",
            return_value="123456789012345678",
        ), patch(
            "app.services.sim_cycle_discord.build_export_team_lists",
            return_value={"exported": [], "pending": []},
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
            "app.services.sim_cycle_discord.build_export_team_lists",
            return_value={"exported": [], "pending": []},
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
            cycle_started_at=datetime.utcnow(),
            updated_at=None,
        )
        session.scalar.return_value = state
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
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

    def test_record_ack_skips_live_start_without_sim_log_route(self) -> None:
        session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            finalize_on_ack=True,
            cycle_started_at=datetime.utcnow(),
            updated_at=None,
        )
        session.scalar.return_value = state
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=False,
        ), patch(
            "app.services.sim_cycle_discord.restart_sim_cycle_after_close_ack"
        ) as restart_mock:
            record_sim_cycle_discord_ack(
                session,
                event_key="sim_cycle_update",
                payload={"source_id": "bowl-cap", "finalize_on_ack": True},
                discord_message_id="123456789012345678",
                discord_channel_id="999",
            )
        restart_mock.assert_not_called()
        self.assertFalse(state.finalize_on_ack)

    def test_restart_after_close_starts_fresh_live_cycle(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        anchor = datetime(2026, 7, 3, 17, 30, 0)
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            cycle_started_at=anchor,
        )
        site_session.scalar.return_value = state
        with patch(
            "app.services.sim_cycle_discord.reset_sim_cycle_state"
        ) as reset_mock, patch(
            "app.services.sim_cycle_discord.start_sim_cycle"
        ) as start_mock:
            reset_mock.return_value = SimpleNamespace(phase="idle")
            start_mock.return_value = (SimpleNamespace(phase="live"), True)
            state, started = restart_sim_cycle_after_close_ack(
                site_session, league_session, "bowl-cap"
            )
        reset_mock.assert_called_once_with(site_session, "bowl-cap")
        start_mock.assert_called_once_with(
            site_session,
            league_session,
            "bowl-cap",
            export_date=datetime.utcnow().date(),
            cycle_started_at=anchor,
        )
        self.assertTrue(started)
        self.assertEqual(state.phase, "live")

    def test_maybe_enqueue_skips_live_phase(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            discord_payload_hash=None,
            updated_at=None,
        )
        with patch(
            "app.services.sim_cycle_discord.enqueue_repeatable_discord_event"
        ) as enqueue_mock:
            queued = maybe_enqueue_sim_cycle_discord(
                site_session, league_session, state, force=True
            )
        self.assertFalse(queued)
        enqueue_mock.assert_not_called()

    def test_start_sim_cycle_does_not_enqueue_discord(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="idle",
            export_date=None,
            cycle_started_at=None,
            discord_message_id=None,
            discord_channel_id=None,
            discord_payload_hash=None,
            tracker_last_message_id=None,
            live_exported_fhm_team_ids_json="[]",
            finalize_on_ack=False,
            updated_at=None,
        )
        with patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch(
            "app.services.sim_cycle_discord.maybe_enqueue_sim_cycle_discord"
        ) as enqueue_mock:
            _state, started = start_sim_cycle(
                site_session, league_session, "bowl-cap"
            )
        self.assertTrue(started)
        self.assertEqual(_state.phase, "live")
        enqueue_mock.assert_not_called()

    def test_publish_closed_sets_finalize_on_ack(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="live",
            export_date=None,
            finalize_on_ack=False,
            discord_payload_hash=None,
            updated_at=None,
        )
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
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
        self.assertTrue(state.finalize_on_ack)
        self.assertTrue(enqueue_mock.call_args.kwargs.get("post_new_message"))
        self.assertTrue(enqueue_mock.call_args.kwargs.get("finalize_on_ack"))

    def test_publish_closed_requeues_when_already_closed(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            export_date=date(2026, 7, 1),
            finalize_on_ack=False,
            discord_payload_hash="old-hash",
            updated_at=None,
            cycle_started_at=datetime(2026, 7, 1, 12, 0, 0),
        )
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.get_or_create_sim_cycle_state",
            return_value=state,
        ), patch(
            "app.services.sim_cycle_discord.maybe_enqueue_sim_cycle_discord",
            return_value=True,
        ) as enqueue_mock:
            ok = publish_closed_sim_cycle_from_admin_export(
                site_session,
                league_session,
                "bowl-cap",
                date(2026, 7, 6),
            )
        self.assertTrue(ok)
        self.assertEqual(state.phase, "closed")
        self.assertEqual(state.export_date, date(2026, 7, 6))
        self.assertTrue(state.finalize_on_ack)
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

    def test_sim_cycle_routes_ready_requires_tracker(self) -> None:
        site_session = MagicMock()
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            side_effect=lambda _s, slug: slug == "bowl-cap",
        ), patch(
            "app.services.sim_cycle_discord.sim_cycle_tracker_route_ready",
            return_value=False,
        ):
            self.assertFalse(sim_cycle_routes_ready(site_session, "bowl-cap"))
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.sim_cycle_tracker_route_ready",
            return_value=True,
        ):
            self.assertTrue(sim_cycle_routes_ready(site_session, "bowl-cap"))

    def test_force_start_live_from_closed_phase(self) -> None:
        site_session = MagicMock()
        league_session = MagicMock()
        state = SimpleNamespace(
            league_slug="bowl-cap",
            phase="closed",
            cycle_started_at=datetime(2026, 7, 5, 12, 0, 0),
            finalize_on_ack=True,
        )
        site_session.scalar.return_value = state
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ), patch(
            "app.services.sim_cycle_discord.restart_sim_cycle_after_close_ack",
            return_value=(SimpleNamespace(phase="live"), True),
        ) as restart_mock:
            ok, message = force_start_live_sim_cycle(
                site_session, league_session, "bowl-cap"
            )
        self.assertTrue(ok)
        self.assertIn("Started live sim cycle", message)
        restart_mock.assert_called_once_with(site_session, league_session, "bowl-cap")

    def test_force_start_live_rejects_when_not_closed(self) -> None:
        site_session = MagicMock()
        site_session.scalar.return_value = SimpleNamespace(phase="live")
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=True,
        ):
            ok, message = force_start_live_sim_cycle(
                site_session, MagicMock(), "bowl-cap"
            )
        self.assertFalse(ok)
        self.assertIn("already live", message.lower())

    def test_force_start_live_requires_sim_log_route(self) -> None:
        site_session = MagicMock()
        with patch(
            "app.services.sim_cycle_discord.sim_log_route_ready",
            return_value=False,
        ):
            ok, message = force_start_live_sim_cycle(
                site_session, MagicMock(), "bowl-cap"
            )
        self.assertFalse(ok)
        self.assertIn("sim log route", message.lower())

    def test_tracker_route_ready_uses_channel_id(self) -> None:
        site_session = MagicMock()
        with patch(
            "app.services.sim_cycle_discord._tracker_channel_id",
            return_value="123456789012345678",
        ):
            self.assertTrue(sim_cycle_tracker_route_ready(site_session, "bowl-cap"))


class SimCycleFormatterTests(unittest.TestCase):
    def test_formatter_builds_export_rows_and_footer(self) -> None:
        event = {
            "league_slug": "bowl-cap",
            "event_key": "sim_cycle_update",
            "payload": {
                "title": "Current Sim Cycle (live)",
                "phase": "live",
                "exported": [17],
                "pending": [3],
                "exported_count": 1,
                "total_teams": 2,
                "last_updated_at": "2026-07-03T04:01:00+00:00",
                "embed_color": 0xB91C1C,
            },
        }
        bodies = format_discord_messages(event, max_parts=1)
        embed = bodies[0]["embeds"][0]
        self.assertIn("In progress", embed["footer"]["text"])
        self.assertIn("1/2 exported", embed["footer"]["text"])
        self.assertNotIn("Atlantic", embed["description"])

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
