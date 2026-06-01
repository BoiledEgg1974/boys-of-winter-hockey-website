"""Headlines URL helpers (mount prefix + deep links)."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config
from app.league_urls import league_mount_relative_path, league_test_request_context
from flask import url_for


class HeadlinesMountPathTest(unittest.TestCase):
    def test_around_the_league_headlines_path_is_mount_relative(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with league_test_request_context(app):
            raw = str(url_for("main.league_headlines"))
            self.assertEqual(raw, "/bowl-fantasy/league-headlines")
            rel = league_mount_relative_path(raw)
            self.assertEqual(rel, "/league-headlines")

    def test_league_mount_relative_path_idempotent_for_prefixed_paths(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with league_test_request_context(app):
            mount = "/bowl-historical"
            self.assertEqual(
                league_mount_relative_path(f"{mount}/league-headlines"),
                "/league-headlines",
            )
            self.assertEqual(league_mount_relative_path("/league-headlines"), "/league-headlines")


if __name__ == "__main__":
    unittest.main()
