"""Prospect page logo regression tests."""

from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config


class ProspectLogoTest(unittest.TestCase):
    def test_historical_prospects_use_timeline_team_logo(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            r = client.get("/prospects")
            self.assertEqual(r.status_code, 200)
            body = r.get_data(as_text=True)

        self.assertIn("pittsburgh_penguins_1968-1971.png", body)
        self.assertNotIn("pit-t122.png", body)

    def test_cap_prospects_page_renders_with_timeline_logo_context(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_client() as client:
            r = client.get("/prospects")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Prospect Rankings", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

