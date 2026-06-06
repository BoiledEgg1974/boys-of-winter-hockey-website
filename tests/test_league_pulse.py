"""League Pulse homepage payload."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.league_pulse import build_league_pulse_payload


class LeaguePulseTest(unittest.TestCase):
    @patch("app.services.league_pulse.get_current_season")
    def test_empty_when_no_season(self, mock_season) -> None:
        mock_season.return_value = None
        out = build_league_pulse_payload(MagicMock(), league_slug="bowl-cap")
        self.assertIsNone(out["spotlight_game"])
        self.assertEqual(out["milestone_watch"], [])

    @patch("app.services.league_pulse.build_draft_hub_tracker")
    @patch("app.services.league_pulse.active_buying_rows")
    @patch("app.services.league_pulse.active_selling_rows")
    @patch("app.services.league_pulse.build_milestone_sections")
    @patch("app.services.league_pulse.build_trending_players")
    @patch("app.services.league_pulse.build_trending_teams")
    @patch("app.services.league_pulse.pick_next_game_to_watch")
    @patch("app.services.league_pulse.pick_game_of_the_night")
    @patch("app.services.league_pulse.league_calendar_anchor_date")
    @patch("app.services.league_pulse.build_conf_cutoff_map")
    @patch("app.services.league_pulse.season_with_imported_data_fallback")
    @patch("app.services.league_pulse.get_current_season")
    def test_payload_aggregates_storylines(
        self,
        mock_get_season,
        mock_fallback,
        _cutoff,
        _cal,
        mock_gotn,
        mock_ngw,
        mock_tt,
        mock_tp,
        mock_ms,
        mock_sell,
        mock_buy,
        mock_tracker,
    ) -> None:
        season = MagicMock()
        season.id = 9
        season.start_year = 2025
        mock_get_season.return_value = season
        mock_fallback.return_value = season
        session = MagicMock()
        session.scalars.return_value.all.return_value = []
        mock_gotn.return_value = {"id": 1, "away_name": "A", "home_name": "B"}
        mock_ngw.return_value = None
        mock_tt.return_value = {"hot": [{"team": "Hot", "team_slug": "hot"}]}
        mock_tp.return_value = {"hot": [{"player": "Star", "player_id": 5}]}
        player = MagicMock()
        player.id = 12
        player.full_name = "Chaser"
        mock_ms.return_value = (
            [MagicMock(title="Goals", rows=[MagicMock(player=player, current_value=98, next_milestone=100, remaining=2)])],
            [],
        )
        mock_sell.return_value = [{"updated_at": None}]
        mock_buy.return_value = []
        mock_tracker.return_value = {"highest_pick_value": {"value": 42.0, "teams": [{"team_abbr": "TOR"}]}}

        out = build_league_pulse_payload(session, league_slug="bowl-cap")
        self.assertEqual(out["spotlight_game"]["id"], 1)
        self.assertEqual(out["hot_team"]["team"], "Hot")
        self.assertEqual(out["player_on_fire"]["player_id"], 5)
        self.assertEqual(len(out["milestone_watch"]), 1)
        self.assertEqual(out["draft_pick_value_leader"]["value"], 42.0)


if __name__ == "__main__":
    unittest.main()
