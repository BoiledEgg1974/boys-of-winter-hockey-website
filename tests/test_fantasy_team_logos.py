"""BOWL-Fantasy roster team logos resolve to real static files."""
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


@unittest.skipUnless(_BOW_DB.is_file(), "instance/bow.db required")
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

    def test_all_roster_teams_resolve_non_placeholder_urls(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        conn = sqlite3.connect(_BOW_DB)
        rows = conn.execute(
            "SELECT slug, name, abbreviation, fhm_team_id FROM teams ORDER BY slug"
        ).fetchall()
        conn.close()

        with app.app_context():
            with app.test_request_context(
                path="/", base_url="http://127.0.0.1/bowl-fantasy/"
            ):
                bundle = get_season_team_logo_bundle(app)
                for slug, name, abbr, fid in rows:
                    team = type(
                        "Team",
                        (),
                        {
                            "slug": slug,
                            "name": name,
                            "abbreviation": abbr,
                            "fhm_team_id": fid,
                        },
                    )()
                    roster_url = team_logo_url_for_team(team)
                    era_url = bundle.team_logo_url_for_season_context(team, 1986)
                    self.assertNotIn(
                        "placeholder",
                        roster_url,
                        msg=f"{slug} roster logo",
                    )
                    self.assertNotIn(
                        "placeholder",
                        era_url,
                        msg=f"{slug} era logo",
                    )
                    self.assertIn(
                        "/bowl-fantasy/static/",
                        roster_url,
                        msg=f"{slug} mount prefix",
                    )

    def test_manifest_covers_database_slugs(self) -> None:
        conn = sqlite3.connect(_BOW_DB)
        slugs = {r[0] for r in conn.execute("SELECT slug FROM teams")}
        conn.close()
        self.assertEqual(set(FANTASY_ROSTER_LOGO_FILES), slugs)


if __name__ == "__main__":
    unittest.main()
