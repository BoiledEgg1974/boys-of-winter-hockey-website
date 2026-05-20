"""BOWL Six Discord leader-board payloads."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.services.bowl_six_discord import build_bowl_six_leaders_discord_payload
from app.services.discord_events import bowl_six_leaders_idempotency_key
from app.site_models import BowlSixSlate


class BowlSixDiscordPayloadTest(unittest.TestCase):
    def test_build_payload_sections(self):
        slate = BowlSixSlate(
            id=7,
            league_slug="bowl-historical",
            week_start=date(1986, 10, 6),
            week_end=date(1986, 10, 12),
            lock_at=datetime.utcnow() + timedelta(days=1),
            status="open",
            label="Week 3",
        )
        with patch(
            "app.services.bowl_six_discord.top_players_for_slate",
            return_value=[],
        ), patch(
            "app.services.bowl_six_discord.slate_rankings_in_progress",
            return_value=[],
        ), patch(
            "app.services.bowl_six_discord.gm_season_standings",
            return_value=[],
        ), patch(
            "app.services.bowl_six_discord.build_league_public_url",
            return_value="https://www.bowlhockey.com/bowl-historical/bowl-six",
        ):
            payload = build_bowl_six_leaders_discord_payload(
                unittest.mock.MagicMock(),
                unittest.mock.MagicMock(),
                slate,
            )
        self.assertEqual(payload["slate_id"], 7)
        self.assertIn("Top performers", payload["body"])
        self.assertEqual(payload["source_id"], "7")

    def test_idempotency_key_per_league_slate(self):
        self.assertEqual(
            bowl_six_leaders_idempotency_key(league_slug="bowl-cap", slate_id=3),
            "bowl-six-leaders:bowl-cap:3",
        )


if __name__ == "__main__":
    unittest.main()
