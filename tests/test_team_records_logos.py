"""Team Records era logo resolution."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app import create_app
from app.config import make_league_config
from app.services.season_team_logo_bundle import get_season_team_logo_bundle


class TeamRecordsLogoTests(unittest.TestCase):
    def test_philadelphia_quakers_static_name_fallback_for_dated_record(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        record = SimpleNamespace(
            team=None,
            team_name_override="Philadelphia Quakers",
            season_year_label="1930-31",
            start_year=1930,
            season_year=1930,
            team_fhm_id_csv=None,
            team_fhm_id=None,
            logo_file_override=None,
        )
        with app.app_context():
            with app.test_request_context(path="/team-records", base_url="http://127.0.0.1/bowl-historical/"):
                logo_url = get_season_team_logo_bundle(app).season_team_logo_url(record)
        self.assertIsNotNone(logo_url)
        self.assertIn("philadelphia_quakers.png", logo_url)

    def test_toronto_1921_season_card_uses_st_pats_logo(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        team = SimpleNamespace(
            id=3,
            slug="tor-t3",
            name="Toronto",
            city="Toronto",
            nickname="Maple Leafs",
            abbreviation="TOR",
            fhm_team_id="3",
        )
        record = SimpleNamespace(
            team=team,
            team_name_override=None,
            season_year_label="1921-22",
            start_year=1921,
            season_year=1921,
            team_fhm_id_csv="3",
            team_fhm_id="3",
            logo_file_override=None,
        )
        with app.app_context():
            with app.test_request_context(path="/team-records", base_url="http://127.0.0.1/bowl-historical/"):
                logo_url = get_season_team_logo_bundle(app).season_team_logo_url(record)
        self.assertIsNotNone(logo_url)
        self.assertIn("toronto_st_pats_1919-1921.png", logo_url)

    def test_toronto_1917_season_card_uses_earliest_toronto_logo(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        team = SimpleNamespace(
            id=3,
            slug="tor-t3",
            name="Toronto",
            city="Toronto",
            nickname="Maple Leafs",
            abbreviation="TOR",
            fhm_team_id="3",
        )
        record = SimpleNamespace(
            team=team,
            team_name_override=None,
            season_year_label="1917-18",
            start_year=1917,
            season_year=1917,
            team_fhm_id_csv="3",
            team_fhm_id="3",
            logo_file_override=None,
        )
        with app.app_context():
            with app.test_request_context(path="/team-records", base_url="http://127.0.0.1/bowl-historical/"):
                logo_url = get_season_team_logo_bundle(app).season_team_logo_url(record)
        self.assertIsNotNone(logo_url)
        self.assertIn("toronto_st_pats_1919-1921.png", logo_url)

    def test_detroit_1931_leaderboard_row_uses_falcons_logo(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        team = SimpleNamespace(
            id=9,
            slug="det-t9",
            name="Detroit",
            city="Detroit",
            nickname="Red Wings",
            abbreviation="DET",
            fhm_team_id="9",
        )
        record = SimpleNamespace(
            team=team,
            team_name_override=None,
            season_year_label="1931-32",
            start_year=1931,
            season_year=1931,
            team_fhm_id_csv="9",
            team_fhm_id="9",
            logo_file_override=None,
        )
        with app.app_context():
            with app.test_request_context(path="/team-records", base_url="http://127.0.0.1/bowl-historical/"):
                logo_url = get_season_team_logo_bundle(app).season_team_logo_url(record)
        self.assertIsNotNone(logo_url)
        self.assertIn("detroit_falcons.png", logo_url)


    def test_cap_expansion_teams_resolve_2000_01_logos(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        teams = [
            (
                "229",
                "CBS",
                "Columbus",
                "Blue Jackets",
                "columbus_blue_jackets_2000-2006.png",
            ),
            (
                "230",
                "MIN",
                "Minnesota",
                "Wild",
                "minnesota_wild_2000-2012.png",
            ),
        ]
        with app.app_context():
            with app.test_request_context(path="/", base_url="http://127.0.0.1/bowl-cap/"):
                bundle = get_season_team_logo_bundle(app)
                for fhm, abbr, city, nick, logo_file in teams:
                    team = SimpleNamespace(
                        id=int(fhm),
                        slug=f"{abbr.lower()}-t{fhm}",
                        name=city,
                        city=city,
                        nickname=nick,
                        abbreviation=abbr,
                        fhm_team_id=fhm,
                    )
                    team.full_display_name = lambda c=city, n=nick: f"{c} {n}"
                    logo_url = bundle.team_logo_url_for_season_context(team, 2000)
                    self.assertIsNotNone(logo_url)
                    self.assertIn(logo_file, logo_url)
                    self.assertNotIn("placeholder", logo_url)

    def test_cap_expansion_teams_team_logo_url_for_team_uses_era_art(self) -> None:
        from app.logo_urls import team_logo_url_for_team

        app = create_app(make_league_config("bowl-cap"))
        team = SimpleNamespace(
            id=229,
            slug="cbs-t229",
            name="Columbus",
            city="Columbus",
            nickname="Blue Jackets",
            abbreviation="CBS",
            fhm_team_id="229",
        )
        team.full_display_name = lambda: "Columbus Blue Jackets"
        with app.app_context():
            with app.test_request_context(path="/", base_url="http://127.0.0.1/bowl-cap/"):
                logo_url = team_logo_url_for_team(team)
        self.assertIn("columbus_blue_jackets_2000-2006.png", logo_url)
        self.assertNotIn("placeholder", logo_url)


if __name__ == "__main__":
    unittest.main()
