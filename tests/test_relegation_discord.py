"""BLUP/BLOW Discord scoping on bowl-fantasy."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.relegation_discord import (
    relegation_discord_enqueue_allowed,
    relegation_discord_game_eligible,
    relegation_discord_team_id_ok,
)


class RelegationDiscordScopeTest(unittest.TestCase):
    def test_historical_mount_unfiltered(self) -> None:
        session = MagicMock()
        self.assertTrue(
            relegation_discord_team_id_ok(session, league_slug="bowl-historical", team_id=999)
        )

    def test_farm_team_blocked_on_relegation(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.relegation_discord.relegation_main_tier_team_ids",
            return_value=frozenset({10, 11}),
        ):
            self.assertFalse(
                relegation_discord_team_id_ok(session, league_slug="bowl-fantasy", team_id=99)
            )
            self.assertTrue(
                relegation_discord_team_id_ok(session, league_slug="bowl-fantasy", team_id=10)
            )

    def test_game_requires_both_main_tier_teams(self) -> None:
        session = MagicMock()
        game_ok = SimpleNamespace(home_team_id=10, away_team_id=11)
        game_bad = SimpleNamespace(home_team_id=10, away_team_id=99)
        with patch(
            "app.services.relegation_discord.relegation_main_tier_team_ids",
            return_value=frozenset({10, 11}),
        ):
            self.assertTrue(
                relegation_discord_game_eligible(
                    session, league_slug="bowl-fantasy", game=game_ok
                )
            )
            self.assertFalse(
                relegation_discord_game_eligible(
                    session, league_slug="bowl-fantasy", game=game_bad
                )
            )

    def test_enqueue_blocks_farm_boxscore(self) -> None:
        session = MagicMock()
        session.get.return_value = SimpleNamespace(home_team_id=10, away_team_id=99)
        with patch(
            "app.services.relegation_discord.relegation_main_tier_team_ids",
            return_value=frozenset({10, 11}),
        ):
            allowed = relegation_discord_enqueue_allowed(
                session,
                league_slug="bowl-fantasy",
                event_key="game_boxscore",
                payload={"game_id": 1, "team_id": 10},
            )
        self.assertFalse(allowed)

    def test_enqueue_allows_blup_trade_confirmation(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.relegation_discord.relegation_main_tier_team_ids",
            return_value=frozenset({10, 11}),
        ):
            allowed = relegation_discord_enqueue_allowed(
                session,
                league_slug="bowl-fantasy",
                event_key="news_article",
                payload={"team_id": 10, "title": "Trade confirmed"},
            )
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
