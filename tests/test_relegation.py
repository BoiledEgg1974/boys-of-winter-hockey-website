"""BOWL-Relegation tier resolver and scoped routes."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.config import relegation_split_active
from app.services.relegation import (
    RelegationTierConfig,
    filter_standings_by_scope,
    get_tier_config,
    is_relegation_league,
    normalize_relegation_scope,
    relegation_under_construction,
    team_matches_scope,
    team_tier,
)


class RelegationTierResolverTests(unittest.TestCase):
    def test_normalize_scope_defaults_combined(self) -> None:
        self.assertEqual(normalize_relegation_scope(None), "combined")
        self.assertEqual(normalize_relegation_scope("UPPER"), "upper")
        self.assertEqual(normalize_relegation_scope("bogus"), "combined")

    def test_is_relegation_league_only_fantasy_slug(self) -> None:
        self.assertTrue(is_relegation_league("bowl-fantasy"))
        self.assertFalse(is_relegation_league("bowl-cap"))

    def test_split_inactive_by_default(self) -> None:
        self.assertFalse(relegation_split_active("bowl-fantasy"))
        self.assertTrue(relegation_under_construction("bowl-fantasy"))

    def test_conference_fallback_maps_wales_and_campbell(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.side_effect = [
            [SimpleNamespace(fhm_league_id=0, name="BOWL-Fantasy", abbreviation="BOWL")],
            [
                SimpleNamespace(fhm_league_id=0, fhm_conference_id=0, fhm_team_id="3"),
                SimpleNamespace(fhm_league_id=0, fhm_conference_id=1, fhm_team_id="8"),
            ],
        ]
        raw_dir = Path(__file__).resolve().parents[1] / "data" / "imports" / "raw" / "bowl_fantasy"
        with patch("app.services.relegation.bowl_nhl_league_ids", return_value=(0,)):
            cfg = get_tier_config(session, raw_import_dir=raw_dir)
        self.assertEqual(cfg.mode, "conference_id")
        self.assertEqual(cfg.upper_conference_ids, frozenset({0}))
        self.assertEqual(cfg.lower_conference_ids, frozenset({1}))
        self.assertIn("Wales", cfg.upper_label)

    def test_league_id_mode_when_upper_lower_meta_present(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.side_effect = [
            [
                SimpleNamespace(fhm_league_id=0, name="BOWL Upper", abbreviation="BOWL"),
                SimpleNamespace(fhm_league_id=42, name="BOWL Lower", abbreviation="BOWL"),
            ],
            [],
        ]
        with patch("app.services.relegation.bowl_nhl_league_ids", return_value=(0, 42)):
            cfg = get_tier_config(session, raw_import_dir=Path("."))
        self.assertEqual(cfg.mode, "league_id")
        self.assertEqual(cfg.upper_league_ids, frozenset({0}))
        self.assertEqual(cfg.lower_league_ids, frozenset({42}))

    def test_team_tier_and_scope_filter(self) -> None:
        cfg = RelegationTierConfig(
            mode="conference_id",
            upper_league_ids=frozenset(),
            lower_league_ids=frozenset(),
            upper_conference_ids=frozenset({0}),
            lower_conference_ids=frozenset({1}),
            upper_label="Upper League",
            lower_label="Lower League",
            combined_league_ids=(0,),
        )
        upper_team = SimpleNamespace(fhm_league_id=0, fhm_conference_id=0)
        lower_team = SimpleNamespace(fhm_league_id=0, fhm_conference_id=1)
        self.assertEqual(team_tier(upper_team, cfg), "upper")
        self.assertEqual(team_tier(lower_team, cfg), "lower")
        self.assertTrue(team_matches_scope(upper_team, "upper", cfg))
        self.assertFalse(team_matches_scope(upper_team, "lower", cfg))

        st_upper = SimpleNamespace(team=upper_team)
        st_lower = SimpleNamespace(team=lower_team)
        filtered = filter_standings_by_scope([st_upper, st_lower], "upper", cfg)
        self.assertEqual(filtered, [st_upper])


class RelegationRouteTests(unittest.TestCase):
    def test_standings_scope_upper_renders_when_split_active(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["RELEGATION_SPLIT_ACTIVE"] = True
        with app.test_client() as client:
            resp = client.get("/standings?scope=upper")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("relegation-scope-tabs", html)
        self.assertIn("scope=upper", html)

    def test_standings_hides_scope_tabs_while_under_construction(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            html = client.get("/standings").get_data(as_text=True)
        self.assertIn("under construction", html.lower())
        self.assertNotIn("relegation-scope-tabs", html)

    def test_relegation_page_available_on_fantasy(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            resp = client.get("/relegation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"UNDER CONSTRUCTION", resp.data)

    def test_relegation_page_live_when_split_active(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["RELEGATION_SPLIT_ACTIVE"] = True
        with app.test_client() as client:
            resp = client.get("/relegation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"How movement works", resp.data)

    def test_relegation_page_redirects_on_cap(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_client() as client:
            resp = client.get("/relegation", follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def test_config_display_name_relegation(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        self.assertEqual(app.config["LEAGUE_DISPLAY_NAME"], "BOWL-Relegation")

    def test_combined_records_default_without_scope(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "services" / "all_time_records.py").read_text(encoding="utf-8")
        self.assertIn("league_ids: tuple[int, ...] | None = None", text)
        self.assertIn("team_fhm_ids: frozenset[str] | None = None", text)


if __name__ == "__main__":
    unittest.main()
