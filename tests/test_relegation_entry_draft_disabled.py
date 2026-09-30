"""Entry draft routes are disabled on BOWL-Relegation (bowl-fantasy) only."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config
from app.services.relegation import entry_draft_enabled_for_league


class EntryDraftDisabledTests(unittest.TestCase):
    def test_helper(self) -> None:
        self.assertFalse(entry_draft_enabled_for_league("bowl-fantasy"))
        self.assertTrue(entry_draft_enabled_for_league("bowl-cap"))
        self.assertTrue(entry_draft_enabled_for_league("bowl-historical"))

    def test_fantasy_mount_returns_404_for_draft_surfaces(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        client = app.test_client()
        for path in (
            "/draft",
            "/draft-eligible",
            "/draft-hub",
            "/draft-lottery/preview",
        ):
            with self.subTest(path=path):
                resp = client.get(path)
                self.assertEqual(resp.status_code, 404, path)

    def test_historical_mount_draft_eligible_ok(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        client = app.test_client()
        resp = client.get("/draft-eligible")
        self.assertIn(resp.status_code, (200, 302))


if __name__ == "__main__":
    unittest.main()
