"""Homepage SSR bootstrap from summary cache."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import create_app
from app.config import make_league_config
from app.services.homepage_ssr import homepage_ssr_bootstrap_from_cache
from app.services.league_json_cache import CacheEntry


class HomepageSsrTests(unittest.TestCase):
    def test_ssr_disabled_returns_none(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["HOMEPAGE_SSR_LEADERS_STANDINGS"] = False
        with app.app_context():
            self.assertIsNone(homepage_ssr_bootstrap_from_cache())

    def test_ssr_reads_cached_summary_without_build(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["HOMEPAGE_SSR_LEADERS_STANDINGS"] = True
        cached_body = {
            "leaders": {"goals": [{"player": "A", "value": 10}]},
            "standings_by_division": [{"division": "East", "teams": []}],
        }
        with app.app_context():
            with (
                patch(
                    "app.services.seasons.get_current_season",
                    return_value=type("S", (), {"id": 1})(),
                ),
                patch(
                    "app.services.seasons.season_with_imported_data_fallback",
                    return_value=type("S", (), {"id": 1})(),
                ),
                patch(
                    "app.services.homepage_ssr.get_cache_entry",
                    return_value=CacheEntry(body=cached_body, saved_at=0.0, is_fresh=True),
                ) as peek,
            ):
                boot = homepage_ssr_bootstrap_from_cache()
        self.assertIsNotNone(boot)
        assert boot is not None
        self.assertEqual(boot["segment"], "rs")
        self.assertIn("goals", boot["leaders"])
        peek.assert_called_once()

    def test_warm_includes_postseason_odds(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["LEAGUE_JSON_CACHE_WARM_ON_STARTUP"] = True
        calls: list[tuple] = []

        def fake_jsonify_cached(namespace, key_suffix, ttl, builder, **kwargs):
            calls.append((namespace, key_suffix))
            builder()
            return None

        with patch(
            "app.services.homepage_summary_cache.build_homepage_summary_cached",
            return_value=({}, "HIT-FRESH"),
        ):
            with patch(
                "app.services.cached_api_responses.jsonify_cached",
                side_effect=fake_jsonify_cached,
            ):
                with patch(
                    "app.services.seasons.get_current_season",
                    return_value=type("S", (), {"id": 1})(),
                ):
                    with patch(
                        "app.services.seasons.season_with_imported_data_fallback",
                        return_value=type("S", (), {"id": 2})(),
                    ):
                        with patch(
                            "app.services.homepage_leaders.build_homepage_leaders_payload",
                            return_value={"leaders": {}},
                        ):
                            with patch(
                                "app.routes.api._build_homepage_postseason_odds_payload",
                                return_value={},
                            ):
                                from app.services.homepage_summary_cache import (
                                    warm_homepage_summary_cache,
                                )

                                warm_homepage_summary_cache(app)
                                import time

                                time.sleep(0.5)

        odds_calls = [c for c in calls if c[0] == "postseason_odds"]
        self.assertGreaterEqual(len(odds_calls), 3)
        scopes = {c[1][1] for c in odds_calls}
        self.assertEqual(scopes, {"combined", "upper", "lower"})


if __name__ == "__main__":
    unittest.main()
