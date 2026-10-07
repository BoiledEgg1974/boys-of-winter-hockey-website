"""Homepage BLUP/BLOW scoping and Three Stars performance guard."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.homepage_dashboard import build_stars_windows
from app.services.homepage_relegation_filter import resolve_homepage_relegation_scope
from app.services.relegation import RelegationTierConfig


class HomepageRelegationScopeTests(unittest.TestCase):
    def test_combined_scope_limits_to_main_tiers_without_split_flag(self) -> None:
        session = MagicMock()
        teams = [
            SimpleNamespace(id=1, fhm_league_id=0),
            SimpleNamespace(id=2, fhm_league_id=1),
            SimpleNamespace(id=99, fhm_league_id=5),
        ]
        session.scalars.return_value.all.return_value = teams
        cfg = RelegationTierConfig(
            mode="league_id",
            upper_league_ids=frozenset({0}),
            lower_league_ids=frozenset({1}),
            upper_conference_ids=frozenset(),
            lower_conference_ids=frozenset(),
            upper_label="BLUP",
            lower_label="BLOW",
            combined_league_ids=(0, 1),
        )
        with patch("app.services.homepage_relegation_filter.get_tier_config", return_value=cfg):
            scope, team_ids = resolve_homepage_relegation_scope(
                session,
                league_slug="bowl-fantasy",
                raw_scope="combined",
                raw_import_dir=None,
            )
        self.assertEqual(scope, "combined")
        self.assertEqual(team_ids, frozenset({1, 2}))

    def test_stars_windows_deferred_photos_finish_quickly(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["RELEGATION_SPLIT_ACTIVE"] = True
        app.config["LEAGUE_JSON_CACHE_WARM_ON_STARTUP"] = False
        from app.models import db
        from app.services.seasons import get_current_season

        with app.test_request_context("/"):
            season = get_current_season()
            if season is None:
                self.skipTest("no season in test db")
            from datetime import date

            t0 = time.perf_counter()
            build_stars_windows(db.session, int(season.id), date.today())
            elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 15.0, f"stars windows took {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
