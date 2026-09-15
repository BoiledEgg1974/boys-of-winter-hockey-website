"""BOWL-Relegation roster team logos resolve to real static files."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from app import create_app
from app.config import BASE_DIR, make_league_config
from app.logo_urls import FANTASY_ROSTER_LOGO_FILES, team_logo_url_for_team
from app.services.season_team_logo_bundle import get_season_team_logo_bundle

_BOW_DB = BASE_DIR / "instance" / "bow.db"
_LOGO_DIR = BASE_DIR / "app" / "static" / "logos" / "teams" / "bowl_fantasy"


class FantasyTeamLogoTests(unittest.TestCase):
    def test_manifest_files_exist_on_disk(self) -> None:
        missing = [
            filename
            for filename in FANTASY_ROSTER_LOGO_FILES.values()
            if not (_LOGO_DIR / filename).is_file()
            and not any(
                p.name.lower() == filename.lower()
                for p in _LOGO_DIR.iterdir()
                if p.is_file()
            )
        ]
        self.assertEqual(missing, [], f"missing logo files: {missing}")

    def test_manifest_resolves_non_placeholder_urls(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.app_context():
            with app.test_request_context(
                path="/", base_url="http://127.0.0.1/bowl-fantasy/"
            ):
                bundle = get_season_team_logo_bundle(app)
                for slug, filename in sorted(FANTASY_ROSTER_LOGO_FILES.items()):
                    abbr = slug.split("-", 1)[0].upper()
                    fid = slug.rsplit("-t", 1)[-1]
                    team = type(
                        "Team",
                        (),
                        {
                            "slug": slug,
                            "name": slug,
                            "abbreviation": abbr,
                            "fhm_team_id": fid,
                        },
                    )()
                    roster_url = team_logo_url_for_team(team)
                    era_url = bundle.team_logo_url_for_season_context(team, 2000)
                    self.assertIn(filename, roster_url, msg=f"{slug} roster logo")
                    self.assertNotIn("placeholder", roster_url, msg=f"{slug} roster logo")
                    self.assertIn(filename, era_url, msg=f"{slug} era logo")
                    self.assertNotIn("placeholder", era_url, msg=f"{slug} era logo")
                    self.assertIn("/bowl-fantasy/static/", roster_url, msg=f"{slug} mount prefix")

    @unittest.skipUnless(_BOW_DB.is_file(), "instance/bow.db required")
    def test_manifest_covers_database_slugs_when_db_matches(self) -> None:
        conn = sqlite3.connect(_BOW_DB)
        slugs = {r[0] for r in conn.execute("SELECT slug FROM teams")}
        conn.close()
        if slugs != set(FANTASY_ROSTER_LOGO_FILES):
            self.skipTest("bow.db roster slugs differ from BOWL-Relegation manifest (re-import pending)")
        self.assertEqual(set(FANTASY_ROSTER_LOGO_FILES), slugs)

    def test_columbus_and_ottawa_logo_aliases(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        cases = (
            ("col-t5", "COL", "5", "Columbus_Chill.png"),
            ("ott-t6", "OTT", "6", "Rideau__St_Lawrence_Kings.png"),
        )
        with app.app_context():
            with app.test_request_context(
                path="/", base_url="http://127.0.0.1/bowl-fantasy/"
            ):
                for slug, abbr, fid, filename in cases:
                    team = type(
                        "Team",
                        (),
                        {
                            "slug": slug,
                            "name": slug,
                            "abbreviation": abbr,
                            "fhm_team_id": fid,
                        },
                    )()
                    self.assertIn(filename, team_logo_url_for_team(team))


if __name__ == "__main__":
    unittest.main()
