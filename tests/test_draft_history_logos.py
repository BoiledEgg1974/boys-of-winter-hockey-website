"""Draft History logo regression tests."""

from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config


class DraftHistoryLogoTest(unittest.TestCase):
    def test_historical_current_team_logo_uses_current_season_context(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            r = client.get("/draft?year=1968")
            self.assertEqual(r.status_code, 200)
            body = r.get_data(as_text=True)

        self.assertIn("pittsburgh_penguins_1968-1971.png", body)
        self.assertNotIn("pit-t122.png", body)


if __name__ == "__main__":
    unittest.main()

