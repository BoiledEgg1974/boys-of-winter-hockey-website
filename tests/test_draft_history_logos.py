"""Draft History logo regression tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from app import create_app
from app.config import make_league_config


class DraftHistoryLogoTest(unittest.TestCase):
    def test_draft_page_renders_for_historical(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            r = client.get("/draft?year=1969")
            self.assertEqual(r.status_code, 200)

    def test_current_team_column_uses_season_aware_team_logo_helper(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "draft.html"
        text = template.read_text(encoding="utf-8")
        self.assertIn("draft_pick_team_logo_url(pk, tm_fhm=tm_fhm, season_year=draft_year)", text)
        self.assertIn("team_logo_url(ctv.team)", text)
        self.assertNotIn("team_logo_url_present_franchise(ctv.team)", text)

    def test_game_boxscore_payload_uses_dashboard_logo_helper(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "api.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("dashboard_team_logo_url(st_team, logo_year)", text)
        self.assertIn("dashboard_team_logo_url(home, logo_year)", text)
        self.assertIn("dashboard_team_logo_url(away, logo_year)", text)

    def test_player_percentiles_use_stat_season_logo_year(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "services" / "player_percentiles.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("logo_year = int(stat_season.start_year)", text)
        self.assertIn("dashboard_team_logo_url(card_team, logo_year)", text)


if __name__ == "__main__":
    unittest.main()

