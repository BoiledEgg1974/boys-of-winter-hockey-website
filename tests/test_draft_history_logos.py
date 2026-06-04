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

    def test_current_team_column_uses_present_franchise_logo(self) -> None:
        template = Path(__file__).resolve().parents[1] / "app" / "templates" / "draft.html"
        text = template.read_text(encoding="utf-8")
        self.assertIn("draft_pick_team_logo_url(pk, tm_fhm=tm_fhm, season_year=draft_year)", text)
        self.assertIn("team_logo_url_present_franchise(ctv.team)", text)
        self.assertNotIn("team_logo_url_for_season_context(ctv.team, current_team_logo_season)", text)


if __name__ == "__main__":
    unittest.main()

