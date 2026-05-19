"""League JSON cache key and storage helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.league_json_cache import (
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


if __name__ == "__main__":
    unittest.main()
