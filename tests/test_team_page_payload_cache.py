"""Team page Monte Carlo and shot-quality JSON cache wrappers."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.league_json_cache import invalidate_league_json_cache
from app.services.team_page_payload_cache import (
    _MC_NONE_SENTINEL,
    get_team_page_mc_bundle_cached,
    get_team_shot_quality_payload_cached,
)


class TeamPagePayloadCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(make_league_config("bowl-fantasy"))
        invalidate_league_json_cache(league_slug="bowl-fantasy")

    def test_mc_cache_hit_skips_second_build(self) -> None:
        team = MagicMock()
        team.id = 35
        payload = {"n_sims": 800, "projection": {"mean_pts": 90.0}}
        build = MagicMock(return_value=payload)

        with self.app.test_request_context(base_url="http://127.0.0.1/bowl-fantasy/"):
            with patch(
                "app.services.postseason_odds.build_team_page_mc_bundle",
                build,
            ):
                first = get_team_page_mc_bundle_cached(
                    MagicMock(), 1, 35, {35: team}
                )
                second = get_team_page_mc_bundle_cached(
                    MagicMock(), 1, 35, {35: team}
                )
        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(build.call_count, 1)

    def test_mc_cache_stores_none_sentinel(self) -> None:
        team = MagicMock()
        team.id = 35
        build = MagicMock(return_value=None)

        with self.app.test_request_context(base_url="http://127.0.0.1/bowl-fantasy/"):
            with patch(
                "app.services.postseason_odds.build_team_page_mc_bundle",
                build,
            ):
                first = get_team_page_mc_bundle_cached(
                    MagicMock(), 1, 35, {35: team}
                )
                second = get_team_page_mc_bundle_cached(
                    MagicMock(), 1, 35, {35: team}
                )
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(build.call_count, 1)

    def test_shot_quality_cache_json_serializable(self) -> None:
        team = MagicMock()
        team.id = 35
        raw_payload = {
            "team": team,
            "season_id": 9,
            "segment": "rs",
            "gp": 10,
            "sq": {"total": 100, "counts": {}, "shares": {}, "sq_avg": 2.1},
            "players": [],
            "categories": [],
            "league_n": 5,
            "min_shots": 5,
            "league_skater_n": 0,
        }
        build = MagicMock(return_value=raw_payload)

        with self.app.test_request_context(base_url="http://127.0.0.1/bowl-fantasy/"):
            with patch(
                "app.services.advanced_stats.build_team_shot_quality_payload",
                build,
            ):
                out = get_team_shot_quality_payload_cached(
                    MagicMock(), team, 9, segment="rs"
                )
                again = get_team_shot_quality_payload_cached(
                    MagicMock(), team, 9, segment="rs"
                )
        self.assertIs(out.get("team"), team)
        json.dumps({k: v for k, v in out.items() if k != "team"})
        self.assertEqual(build.call_count, 1)
        self.assertEqual(again["season_id"], 9)

    def test_mc_none_sentinel_not_leaked_to_template_shape(self) -> None:
        self.assertNotEqual(_MC_NONE_SENTINEL, "projection")
