"""Single-league deploy-db slug filtering."""
from __future__ import annotations

import unittest

from scripts.STEP2_pythonanywhere import (
    build_post_db_upload_script,
    resolve_deploy_db_leagues,
)


class DeployLeagueFilterTests(unittest.TestCase):
    def test_default_keeps_all_hockey_and_racing(self) -> None:
        hockey, racing = resolve_deploy_db_leagues(
            "",
            all_slugs=["bowl-historical", "bowl-fantasy", "bowl-cap", "bowl-formula"],
            hockey_slugs=["bowl-historical", "bowl-fantasy", "bowl-cap"],
            racing_slugs=["bowl-formula"],
        )
        self.assertEqual(hockey, ["bowl-historical", "bowl-fantasy", "bowl-cap"])
        self.assertEqual(racing, ["bowl-formula"])

    def test_relegation_alias_selects_fantasy_only(self) -> None:
        hockey, racing = resolve_deploy_db_leagues(
            "BOWL-Relegation",
            all_slugs=["bowl-historical", "bowl-fantasy", "bowl-cap"],
            hockey_slugs=["bowl-historical", "bowl-fantasy", "bowl-cap"],
            racing_slugs=[],
        )
        self.assertEqual(hockey, ["bowl-fantasy"])
        self.assertEqual(racing, [])

    def test_post_upload_notify_scopes_to_one_league(self) -> None:
        script = build_post_db_upload_script(
            "/home/u/proj",
            "/home/u/venv/bin",
            ["bowl-fantasy"],
            None,
            staged_db_rels=("instance/bowl-fantasy.db",),
            notify_league="bowl-fantasy",
        )
        self.assertIn("--league bowl-fantasy", script)
        self.assertIn("--stash-live-record-state --league bowl-fantasy", script)


if __name__ == "__main__":
    unittest.main()
