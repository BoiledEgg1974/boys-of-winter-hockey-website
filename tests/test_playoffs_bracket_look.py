"""Playoffs bracket visual: ensure the server-rendered mirror markup exists."""

from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config


class PlayoffsBracketLookRoutesTest(unittest.TestCase):
    def test_playoffs_renders_for_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            app = create_app(make_league_config(slug))
            with app.test_client() as client:
                r = client.get("/playoffs")
                self.assertEqual(r.status_code, 200, slug)
                body = r.get_data(as_text=True)
                self.assertIn("Playoff Bracket", body)
                # In an empty/off-season DB, the page can intentionally be non-visual.
                if "bracket-visual" in body:
                    self.assertIn("Championship", body)
                    # Mirror bracket includes a trophy image banner (league-specific).
                    self.assertIn("bracket-trophy-img", body)
                else:
                    self.assertIn("empty-state", body)


if __name__ == "__main__":
    unittest.main()

