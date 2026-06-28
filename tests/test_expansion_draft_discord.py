"""Expansion Draft public Discord alert helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import app.services.expansion_draft_discord as expansion_draft_discord
from app.models import Team


class ExpansionDraftDiscordTests(unittest.TestCase):
    def test_enqueue_alerts_posts_on_clock_to_expansion_draft(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        team = MagicMock()
        team.full_display_name.return_value = "Seattle Kraken"
        team.name = "Seattle"
        team.abbreviation = "SEA"
        team.fhm_team_id = "3"
        session.get.return_value = team

        draft = MagicMock(
            id=9,
            league_slug="bowl-historical",
            status="live",
            current_slot_index=0,
            timer_seconds=120,
        )
        draft.name = "2026 Expansion Draft"
        current = MagicMock(overall_pick=1, round=1, team_id=10, phase="goalie", forfeited=False)

        with (
            patch("app.services.expansion_draft_state.gm_user_ids_for_team", return_value=[]),
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_name": "Seattle Kraken", "team_abbrev": "SEA"},
            ),
            patch("app.services.discord_events.enqueue_discord_event") as enqueue,
        ):
            expansion_draft_discord.enqueue_expansion_draft_discord_alerts(session, draft, current)

        self.assertEqual(len(enqueue.call_args_list), 1)
        self.assertEqual(enqueue.call_args_list[0].kwargs["event_key"], "expansion_draft_on_clock")
        self.assertEqual(enqueue.call_args_list[0].kwargs["source_type"], "expansion_draft_on_clock")
        self.assertEqual(enqueue.call_args_list[0].kwargs["source_id"], "9:1")
        payload = enqueue.call_args_list[0].kwargs["payload"]
        self.assertEqual(payload["phase"], "Goalie")
        self.assertEqual(payload["overall_pick"], 1)

    def test_expansion_status_message_includes_clock_and_link(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = []

        team = MagicMock(spec=Team)
        team.full_display_name.return_value = "Seattle Kraken"
        team.abbreviation = "SEA"
        team.name = "Seattle"
        session.get.return_value = team

        draft = MagicMock(
            id=9,
            league_slug="bowl-historical",
            status="live",
            awaiting_admin_resolution=False,
            timer_paused=False,
            pick_deadline_at=None,
            current_slot_index=0,
        )
        draft.name = "2026 Expansion Draft"
        current = MagicMock(overall_pick=1, round=1, team_id=10, phase="goalie", forfeited=False)

        with (
            patch.object(expansion_draft_discord, "featured_expansion_draft", return_value=draft),
            patch.object(expansion_draft_discord, "slots_ordered", return_value=[current]),
            patch(
                "app.services.discord_events.build_league_public_url",
                return_value="https://www.bowlhockey.com/bowl-historical/expansion-draft-hub/9",
            ),
        ):
            msg = expansion_draft_discord.build_expansion_status_message(session, "bowl-historical")

        self.assertIn("**2026 Expansion Draft**", msg)
        self.assertIn("On the clock: Seattle Kraken", msg)
        self.assertIn("Goalie phase", msg)
        self.assertIn("expansion-draft-hub/9", msg)

    def test_expansion_status_message_handles_no_live_draft(self) -> None:
        with patch.object(expansion_draft_discord, "featured_expansion_draft", return_value=None):
            msg = expansion_draft_discord.build_expansion_status_message(MagicMock(), "bowl-historical")
        self.assertIn("No live Expansion Draft", msg)


if __name__ == "__main__":
    unittest.main()
