"""Join Our League availability helpers."""
from __future__ import annotations

import tempfile
import unittest

from flask import Flask

from app.services.join_league import (
    WAITLIST_OPTION,
    dedupe_team_options,
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


if __name__ == "__main__":
    unittest.main()
