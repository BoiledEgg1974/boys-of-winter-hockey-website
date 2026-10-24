"""League season records, including per-league rookie boards."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import create_app
from app.config import make_league_config
from app.services.league_season_records import (
    keep_rookie_season_record_row,
    rookie_season_records_from_label,
    rookie_season_records_start_year,
)
from app.services.rookie_eligibility import player_is_rookie_in_season_year


class RookieSeasonRecordEraTest(unittest.TestCase):
    def test_start_years_are_league_specific(self) -> None:
        self.assertEqual(rookie_season_records_start_year(league_slug="bowl-historical"), 1942)
        self.assertEqual(rookie_season_records_start_year(league_slug="bowl-cap"), 1989)

    def test_relegation_uses_earliest_season_row(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            SimpleNamespace(label="2026-27 Season", start_year=2026, end_year=2027),
            SimpleNamespace(label="2027-28 Season", start_year=2027, end_year=2028),
        ]
        self.assertEqual(
            rookie_season_records_start_year(session, league_slug="bowl-fantasy"),
            2026,
        )

    def test_relegation_without_seasons_has_no_floor(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = []
        self.assertEqual(rookie_season_records_start_year(session, league_slug="bowl-fantasy"), 0)
        self.assertEqual(rookie_season_records_from_label(0), "the inaugural season")

    def test_from_label_formats_hockey_season(self) -> None:
        self.assertEqual(rookie_season_records_from_label(1942), "1942–43")
        self.assertEqual(rookie_season_records_from_label(1989), "1989–90")


class RookieSeasonRecordFilterTest(unittest.TestCase):
    def test_drops_years_before_league_floor(self) -> None:
        self.assertFalse(
            keep_rookie_season_record_row(
                season_year=1941,
                gp_by_year={},
                birth_date=date(1922, 1, 1),
                start_year=1942,
            )
        )
        self.assertTrue(
            keep_rookie_season_record_row(
                season_year=1942,
                gp_by_year={},
                birth_date=date(1922, 1, 1),
                start_year=1942,
            )
        )

    def test_veteran_prior_gp_is_not_a_rookie_year(self) -> None:
        self.assertFalse(
            player_is_rookie_in_season_year(
                gp_by_year={1988: 40},
                birth_date=date(1968, 5, 1),
                season_year=1989,
            )
        )
        self.assertTrue(
            player_is_rookie_in_season_year(
                gp_by_year={1988: 4},
                birth_date=date(1968, 5, 1),
                season_year=1989,
            )
        )


class SeasonRecordsRouteTest(unittest.TestCase):
    def test_rookie_filter_tab_renders_on_cap(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_client() as client:
            r = client.get("/season-records?scope=rookie")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"Rookies", r.data)
            self.assertIn(b"1989", r.data)

    def test_historical_rookie_copy_uses_1942(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            r = client.get("/season-records?scope=rookie")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"1942", r.data)
