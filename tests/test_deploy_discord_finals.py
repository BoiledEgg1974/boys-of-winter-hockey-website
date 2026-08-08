"""Tests for deploy-db Discord finals sidecars."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.deploy_discord_finals import (
    clear_deploy_newly_final_game_ids,
    load_deploy_newly_final_game_ids,
    record_deploy_newly_final_game_ids,
)
from app.services.game_boxscore_discord import (
    drain_stashed_newly_final_game_ids,
    notify_game_boxscores_after_import,
    stash_newly_final_game_ids,
)


class DeployDiscordFinalsTest(unittest.TestCase):
    def test_record_merge_load_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                record_deploy_newly_final_game_ids(
                    "bowl-historical", {10, 11}, instance_root=root
                ),
                2,
            )
            self.assertEqual(
                record_deploy_newly_final_game_ids(
                    "bowl-historical", {11, 12}, instance_root=root
                ),
                1,
            )
            self.assertEqual(
                load_deploy_newly_final_game_ids("bowl-historical", instance_root=root),
                {10, 11, 12},
            )
            self.assertTrue(
                clear_deploy_newly_final_game_ids("bowl-historical", instance_root=root)
            )
            self.assertEqual(
                load_deploy_newly_final_game_ids("bowl-historical", instance_root=root),
                set(),
            )

    def test_notify_records_deploy_sidecar(self) -> None:
        drain_stashed_newly_final_game_ids()
        stash_newly_final_game_ids({42, 43})
        recorded: list[tuple[str, set[int]]] = []

        def _capture(slug, ids, instance_root=None):
            recorded.append((slug, set(int(x) for x in ids)))
            return len(ids)

        with patch(
            "app.services.deploy_discord_finals.record_deploy_newly_final_game_ids",
            _capture,
        ), patch(
            "app.services.game_boxscore_discord.record_pending_game_boxscore_ids"
        ), patch(
            "app.services.game_boxscore_discord.list_pending_game_boxscore_ids",
            return_value=[42, 43],
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=False,
        ):
            out = notify_game_boxscores_after_import(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-cap",
            )
        self.assertEqual(out.get("skipped"), 2)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "bowl-cap")
        self.assertEqual(recorded[0][1], {42, 43})
        drain_stashed_newly_final_game_ids()


if __name__ == "__main__":
    unittest.main()
