"""Draft Hub public Discord alert helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import app.services.draft_hub_discord as draft_hub_discord
from app.models import Team


class DraftHubDiscordTests(unittest.TestCase):
    def test_enqueue_alerts_adds_on_clock_and_configured_on_deck(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        team = MagicMock()
        team.full_display_name.return_value = "Toronto Towers"
        team.name = "Toronto"
        team.abbreviation = "TOR"
        team.fhm_team_id = "3"
        session.get.return_value = team

        draft = MagicMock(
            id=7,
            league_slug="bowl-fantasy",
            status="live",
            current_slot_index=0,
            picks_per_round=27,
            timer_seconds=120,
            discord_on_deck_enabled=True,
        )
        draft.name = "2026 Draft Hub"
        current = MagicMock(overall_pick=3, round=1, team_id=10, forfeited=False)
        next_slot = MagicMock(overall_pick=4, round=1, team_id=11, forfeited=False)

        with (
            patch.object(draft_hub_discord, "gm_user_ids_for_team", return_value=[]),
            patch("app.services.discord_events.enqueue_discord_event") as enqueue,
        ):
            draft_hub_discord.enqueue_draft_hub_discord_alerts(session, draft, current, [current, next_slot])

        event_keys = [call.kwargs["event_key"] for call in enqueue.call_args_list]
        self.assertEqual(event_keys, ["draft_hub_on_clock", "draft_hub_on_deck"])
        self.assertEqual(enqueue.call_args_list[0].kwargs["source_type"], "draft_on_clock")
        self.assertEqual(enqueue.call_args_list[0].kwargs["source_id"], "7:3")
        self.assertEqual(enqueue.call_args_list[1].kwargs["source_type"], "draft_on_deck")
        self.assertEqual(enqueue.call_args_list[1].kwargs["source_id"], "7:3")

    def test_draft_status_message_includes_current_on_deck_and_recent_link(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = []

        team_a = MagicMock(spec=Team)
        team_a.full_display_name.return_value = "Toronto Towers"
        team_a.abbreviation = "TOR"
        team_a.name = "Toronto"
        team_b = MagicMock(spec=Team)
        team_b.full_display_name.return_value = "Hamilton Steel"
        team_b.abbreviation = "HAM"
        team_b.name = "Hamilton"

        def get_side_effect(model, ident):
            return team_a if int(ident) == 10 else team_b

        session.get.side_effect = get_side_effect
        draft = MagicMock(
            id=7,
            league_slug="bowl-fantasy",
            status="live",
            awaiting_admin_resolution=False,
            timer_paused=False,
            pick_deadline_at=None,
            current_slot_index=0,
            picks_per_round=27,
        )
        draft.name = "2026 Draft Hub"
        current = MagicMock(overall_pick=3, round=1, team_id=10, forfeited=False)
        next_slot = MagicMock(overall_pick=4, round=1, team_id=11, forfeited=False)

        with (
            patch.object(draft_hub_discord, "featured_draft", return_value=draft),
            patch.object(draft_hub_discord, "slots_ordered", return_value=[current, next_slot]),
            patch("app.services.discord_events.build_league_public_url", return_value="https://www.bowlhockey.com/bowl-fantasy/draft-hub"),
        ):
            msg = draft_hub_discord.build_draft_status_message(session, "bowl-fantasy")

        self.assertIn("**2026 Draft Hub**", msg)
        self.assertIn("On the clock: Toronto Towers", msg)
        self.assertIn("On deck: Hamilton Steel", msg)
        self.assertIn("https://www.bowlhockey.com/bowl-fantasy/draft-hub", msg)

    def test_draft_status_message_handles_no_live_draft(self) -> None:
        with patch.object(draft_hub_discord, "featured_draft", return_value=None):
            msg = draft_hub_discord.build_draft_status_message(MagicMock(), "bowl-fantasy")
        self.assertIn("No live Draft Hub draft", msg)


if __name__ == "__main__":
    unittest.main()
