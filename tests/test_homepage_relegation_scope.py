"""Homepage BLUP/BLOW scoping and Three Stars performance guard."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.services.homepage_dashboard import build_stars_windows
from app.services.homepage_relegation_filter import (
    HOMEPAGE_RELEGATION_SCOPED_PANEL_KEYS,
    filter_standings_by_division_payload,
    homepage_panel_uses_relegation_scope,
    resolve_homepage_relegation_scope,
)
from app.services.relegation import RelegationTierConfig


class HomepageRelegationScopeTests(unittest.TestCase):
    def test_schedule_and_around_league_are_scoped_panels(self) -> None:
        self.assertTrue(homepage_panel_uses_relegation_scope("schedule"))
        self.assertTrue(homepage_panel_uses_relegation_scope("around_the_league"))
        self.assertTrue(homepage_panel_uses_relegation_scope("power_rankings"))
        self.assertTrue(homepage_panel_uses_relegation_scope("divisional_standings"))
        self.assertTrue(homepage_panel_uses_relegation_scope("player_momentum"))
        self.assertIn("league_transactions", HOMEPAGE_RELEGATION_SCOPED_PANEL_KEYS)

    def test_combined_standings_split_blup_blow(self) -> None:
        session = MagicMock()
        upper_team = SimpleNamespace(id=1, fhm_league_id=0)
        lower_team = SimpleNamespace(id=2, fhm_league_id=1)
        session.scalars.return_value.all.return_value = [upper_team, lower_team]
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
        divisions = [
            {
                "division": "League",
                "teams": [
                    {"team_id": 1, "pts": 10, "w": 5, "name": "A"},
                    {"team_id": 2, "pts": 8, "w": 4, "name": "B"},
                ],
            }
        ]
        with patch("app.services.homepage_relegation_filter.get_tier_config", return_value=cfg):
            out = filter_standings_by_division_payload(
                divisions,
                frozenset({1, 2}),
                relegation_scope="combined",
                session=session,
            )
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["division"], "BLUP")
        self.assertEqual(out[1]["division"], "BLOW")
        self.assertEqual([r["team_id"] for r in out[0]["teams"]], [1])
        self.assertEqual([r["team_id"] for r in out[1]["teams"]], [2])

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

    def test_fantasy_summary_schedule_excludes_cross_tier_farm_games(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["LEAGUE_JSON_CACHE_WARM_ON_STARTUP"] = False
        from app.models import Game, Team, db
        from app.services.seasons import get_current_season

        with app.app_context():
            season = get_current_season()
            if season is None:
                self.skipTest("no season in test db")
            farm = db.session.scalar(select(Team).where(Team.fhm_league_id == 2).limit(1))
            main = db.session.scalar(
                select(Team).where(Team.fhm_league_id.in_((0, 1))).limit(1)
            )
            if farm is None or main is None:
                self.skipTest("need farm and main tier teams")
            farm_game = db.session.scalar(
                select(Game)
                .where(
                    Game.season_id == season.id,
                    Game.home_team_id == farm.id,
                    Game.away_team_id == main.id,
                )
                .limit(1)
            )
            if farm_game is None:
                self.skipTest("no cross-tier game in db")
            farm_game_id = int(farm_game.id)
        with app.test_client() as client:
            data = client.get("/api/homepage/summary?segment=rs").get_json()
        games = data.get("games") or []
        upcoming = data.get("upcoming") or []
        ids = {int(g.get("id") or 0) for g in games + upcoming}
        self.assertNotIn(farm_game_id, ids)


if __name__ == "__main__":
    unittest.main()
