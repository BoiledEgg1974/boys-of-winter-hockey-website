"""Join Our League availability helpers."""
from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask

from app.services.join_league import (
    WAITLIST_OPTION,
    dedupe_team_options,
    join_league_available_team_banner_rows,
    join_league_team_options,
    save_join_team_options,
)


class JoinLeagueAvailabilityTests(unittest.TestCase):
    def test_dedupe_omits_waitlist_and_blanks(self) -> None:
        self.assertEqual(
            dedupe_team_options(["", " Waitlist ", "Tokyo Katanas", "tokyo katanas"]),
            ["Tokyo Katanas"],
        )

    def test_fantasy_default_only_when_no_admin_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Flask(__name__, instance_path=tmp)
            app.config["LEAGUE_SLUG"] = "bowl-fantasy"
            with app.app_context():
                self.assertEqual(join_league_team_options(), [WAITLIST_OPTION, "Tokyo Katanas"])
                save_join_team_options([])
                self.assertEqual(join_league_team_options(), [WAITLIST_OPTION])

    def test_saved_open_teams_drive_public_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Flask(__name__, instance_path=tmp)
            app.config["LEAGUE_SLUG"] = "bowl-cap"
            with app.app_context():
                save_join_team_options(["Quebec Nordiques", "Waitlist", "Quebec Nordiques"])
                self.assertEqual(join_league_team_options(), [WAITLIST_OPTION, "Quebec Nordiques"])

    def test_available_team_banner_rows_map_saved_names_to_current_teams(self) -> None:
        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        with tempfile.TemporaryDirectory() as tmp:
            app = Flask(__name__, instance_path=tmp)
            app.config["LEAGUE_SLUG"] = "bowl-cap"
            team = SimpleNamespace(
                name="Quebec",
                nickname="Nordiques",
                slug="quebec-nordiques",
                full_display_name=lambda: "Quebec Nordiques",
            )
            session = MagicMock()
            session.scalars.return_value = _Rows([team])
            with app.app_context():
                save_join_team_options(["Quebec Nordiques"])
                rows = join_league_available_team_banner_rows(session)

        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["team"], team)
        self.assertEqual(rows[0]["label"], "Quebec Nordiques")
        self.assertEqual(rows[0]["slug"], "quebec-nordiques")

    def test_join_availability_banner_template_markers(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        init = (root / "app" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("join_league_available_team_rows", base)
        self.assertIn("TEAM(S) CURRENTLY AVAILABLE:", base)
        self.assertIn("CLICK ON JOIN LEAGUE TODAY!", base)
        self.assertIn("team_logo_url(row.team)", base)
        self.assertIn(".join-availability-banner", css)
        self.assertIn("join_league_available_team_banner_rows", init)


if __name__ == "__main__":
    unittest.main()
