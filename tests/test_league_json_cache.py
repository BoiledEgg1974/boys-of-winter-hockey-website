"""League JSON cache key and storage helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.league_urls import real_flask_app
from app.services.league_json_cache import (
    _schedule_background_refresh,
    cache_key,
    invalidate_league_json_cache,
)


class LeagueJsonCacheTests(unittest.TestCase):
    def test_cache_key_includes_namespace_and_slugs(self) -> None:
        app = MagicMock()
        app.config = {"LEAGUE_SLUG": "bowl-cap"}
        with patch("app.services.league_json_cache.league_db_fingerprint", return_value="league:1"):
            with patch("app.services.league_json_cache.site_db_fingerprint", return_value="site:2"):
                key = cache_key("playoff_bracket", (99,), app=app)
        self.assertEqual(key[0], "playoff_bracket")
        self.assertEqual(key[1], "bowl-cap")
        self.assertEqual(key[4], 99)

    def test_invalidate_namespace_filter(self) -> None:
        invalidate_league_json_cache(league_slug="bowl-fantasy", namespace="search_players")

    def test_real_flask_app_resolves_current_app_proxy(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.app_context():
            from flask import current_app

            bound = real_flask_app(current_app)
            self.assertIs(bound, app)

    def test_background_refresh_runs_in_app_context(self) -> None:
        import threading
        from unittest.mock import patch

        app = create_app(make_league_config("bowl-fantasy"))
        done = threading.Event()
        captured: dict[str, object] = {}

        def builder() -> dict:
            from flask import current_app

            captured["slug"] = current_app.config.get("LEAGUE_SLUG")
            done.set()
            return {"ok": True}

        with app.test_request_context(
            path="/", base_url="http://127.0.0.1/bowl-fantasy/"
        ):
            from flask import current_app

            with patch(
                "app.services.league_json_cache.store_cached_json"
            ) as store_mock:
                _schedule_background_refresh(
                    current_app, "homepage_summary", ("rs", 1, 1), builder
                )
                self.assertTrue(done.wait(10), "background refresh did not finish")
                store_mock.assert_called_once()
                self.assertEqual(captured.get("slug"), "bowl-fantasy")


if __name__ == "__main__":
    unittest.main()
