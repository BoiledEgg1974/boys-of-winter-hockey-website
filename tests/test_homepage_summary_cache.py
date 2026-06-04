"""Homepage summary cache helpers."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import create_app
from app.config import make_league_config
from app.services.homepage_summary_cache import _strip_volatile_fields, build_homepage_summary_cached


class HomepageSummaryCacheTests(unittest.TestCase):
    def test_strip_volatile_fields(self) -> None:
        body = {
            "leaders": {"goals": []},
            "around_the_league": {"articles": []},
            "module_settings": {"visibility": {}},
            "ticker_items": [{"text": "x"}],
        }
        core = _strip_volatile_fields(body)
        self.assertIn("leaders", core)
        self.assertIn("around_the_league", core)
        self.assertNotIn("module_settings", core)
        self.assertNotIn("ticker_items", core)

    def test_homepage_summary_refreshes_stale_cache_in_background(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.app_context():
            with (
                patch(
                    "app.services.homepage_summary_cache.get_or_build_cached_json_swr",
                    return_value=({"leaders": {"goals": []}}, "HIT-STALE"),
                ) as cached,
                patch(
                    "app.services.homepage_summary_cache.refresh_volatile_homepage_fields",
                    side_effect=lambda body: body,
                ),
            ):
                body, status = build_homepage_summary_cached("rs", None, None, lambda: {})

        self.assertEqual(status, "HIT-STALE")
        self.assertIn("leaders", body)
        self.assertTrue(cached.call_args.kwargs["refresh_stale_in_background"])

    def test_season_logo_bundle_uses_request_cache_before_fingerprint(self) -> None:
        import app.services.season_team_logo_bundle as logos

        app = create_app(make_league_config("bowl-cap"))
        with app.test_request_context("/"):
            with patch.object(
                logos,
                "_bundle_input_fingerprint",
                wraps=logos._bundle_input_fingerprint,
            ) as fp:
                first = logos.get_season_team_logo_bundle(app)
                second = logos.get_season_team_logo_bundle(app)

        self.assertIs(first, second)
        self.assertEqual(fp.call_count, 1)


if __name__ == "__main__":
    unittest.main()
