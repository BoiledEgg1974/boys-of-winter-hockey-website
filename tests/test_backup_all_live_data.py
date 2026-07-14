"""Tests for scripts/backup_all_live_data.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import backup_all_live_data as backup


def _create_league_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE history_awards (
                id INTEGER PRIMARY KEY,
                season_label TEXT,
                award_name TEXT
            );
            CREATE TABLE history_champions (
                id INTEGER PRIMARY KEY,
                season_label TEXT
            );
            CREATE TABLE history_all_stars (
                id INTEGER PRIMARY KEY,
                season_label TEXT
            );
            CREATE TABLE hall_of_fame_members (
                id INTEGER PRIMARY KEY,
                player_name TEXT NOT NULL
            );
            CREATE TABLE game_record_baselines (
                id INTEGER PRIMARY KEY,
                category TEXT
            );
            CREATE TABLE team_season_records (
                id INTEGER PRIMARY KEY,
                season_label TEXT
            );
            CREATE TABLE record_stat_adjustments (
                id INTEGER PRIMARY KEY,
                note TEXT
            );
            CREATE TABLE seasons (
                id INTEGER PRIMARY KEY,
                label TEXT,
                is_current INTEGER
            );
            CREATE TABLE team_standings (
                id INTEGER PRIMARY KEY,
                season_id INTEGER
            );
            CREATE TABLE player_skater_stats (
                id INTEGER PRIMARY KEY,
                season_id INTEGER
            );
            CREATE TABLE player_goalie_stats (
                id INTEGER PRIMARY KEY,
                season_id INTEGER
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                season_id INTEGER
            );
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                fhm_player_id TEXT
            );
            CREATE TABLE player_overall_baselines (
                player_id INTEGER PRIMARY KEY,
                baseline_score INTEGER
            );
            INSERT INTO history_awards (id, season_label, award_name)
            VALUES (1, '2024-25', 'MVP');
            INSERT INTO hall_of_fame_members (id, player_name) VALUES (1, 'Test Player');
            INSERT INTO seasons (id, label, is_current) VALUES (1, '2024-25', 1);
            INSERT INTO game_record_baselines (id, category) VALUES (1, 'goals');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _create_site_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE ap_redemption_catalog (
                id INTEGER PRIMARY KEY,
                league_group TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                description TEXT,
                cost_ap INTEGER NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE boost_lottery_team_results (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL,
                team_key TEXT NOT NULL,
                gold_count INTEGER NOT NULL DEFAULT 0,
                silver_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE league_rule_settings (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value TEXT
            );
            CREATE TABLE league_salary_cap_years (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL,
                season_year INTEGER NOT NULL
            );
            CREATE TABLE homepage_module_settings (
                id INTEGER PRIMARY KEY,
                module_key TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE site_announcements (
                id INTEGER PRIMARY KEY,
                body TEXT NOT NULL
            );
            CREATE TABLE discord_league_bot_config (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL
            );
            CREATE TABLE discord_channel_routes (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL,
                route_key TEXT NOT NULL
            );
            CREATE TABLE sim_cycle_state (
                id INTEGER PRIMARY KEY,
                league_slug TEXT NOT NULL,
                status TEXT
            );
            INSERT INTO ap_redemption_catalog
            (id, league_group, sort_order, title, description, cost_ap, is_active)
            VALUES (1, 'bowl', 1, 'Test item', 'desc', 5, 1);
            INSERT INTO boost_lottery_team_results
            (id, league_slug, team_key, gold_count, silver_count)
            VALUES (1, 'bowl-cap', 'team-a', 2, 1);
            INSERT INTO league_rule_settings
            (id, league_slug, rule_key, rule_value)
            VALUES (1, 'bowl-cap', 'draft_eligible_min_age_years', '18');
            """
        )
        conn.commit()
    finally:
        conn.close()


class BackupAllLiveDataTests(unittest.TestCase):
    def test_copy_sqlite_database_includes_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "sample.db"
            conn = sqlite3.connect(str(src))
            try:
                conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO teams (id) VALUES (1)")
                conn.commit()
            finally:
                conn.close()
            (root / "sample.db-wal").write_bytes(b"wal")
            (root / "sample.db-shm").write_bytes(b"shm")

            dest = root / "out" / "sample.db"
            info = backup.copy_sqlite_database(src, dest, checkpoint=False)

            self.assertTrue(info["ok"])
            self.assertTrue(dest.is_file())
            self.assertTrue((root / "out" / "sample.db-wal").is_file())
            self.assertTrue((root / "out" / "sample.db-shm").is_file())

    def test_run_backup_writes_manifest_and_site_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_db = root / "site_membership.db"
            _create_site_db(site_db)

            out_dir = root / "backup"
            with patch.object(backup, "resolve_site_sqlite_path", return_value=site_db), patch.object(
                backup,
                "backup_league_databases",
                return_value=[{"slug": "bowl-fantasy", "ok": False, "message": "source missing"}],
            ), patch.dict("os.environ", {}, clear=True):
                manifest = backup.run_backup(
                    out_dir,
                    checkpoint=False,
                    include_json=True,
                    instance_dir=root,
                )

            self.assertTrue((out_dir / "backup_manifest.json").is_file())
            self.assertTrue(manifest["site"]["ok"])
            tables_dir = out_dir / "site" / "tables"
            catalog_path = tables_dir / "ap_redemption_catalog.json"
            self.assertTrue(catalog_path.is_file())
            rows = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "Test item")

            site_json = out_dir / "json" / "site"
            boost_path = site_json / "boost_records.json"
            admin_path = site_json / "admin_settings.json"
            self.assertTrue(boost_path.is_file())
            self.assertTrue(admin_path.is_file())
            boost_rows = json.loads(boost_path.read_text(encoding="utf-8"))
            self.assertEqual(len(boost_rows), 1)
            admin = json.loads(admin_path.read_text(encoding="utf-8"))
            self.assertEqual(admin["league_rule_settings"][0]["rule_key"], "draft_eligible_min_age_years")

            coverage_site = manifest["coverage"]["site"]
            self.assertTrue(coverage_site["ok"])
            self.assertEqual(coverage_site["categories"]["boost_records"]["total_rows"], 1)

    def test_run_backup_inventory_history_json_and_join_league(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_db = root / "site_membership.db"
            league_db = root / "bowl-cap.db"
            _create_site_db(site_db)
            _create_league_db(league_db)

            join_file = root / "join_league" / "bowl-cap" / "available_teams.txt"
            join_file.parent.mkdir(parents=True)
            join_file.write_text("Alpha Team\n", encoding="utf-8")
            legacy = root / "join_league_available_teams.txt"
            legacy.write_text("Legacy Team\n", encoding="utf-8")

            out_dir = root / "backup"

            def _fake_league_backup(out: Path, *, checkpoint: bool = True, verify: bool = False) -> list[dict]:
                dest = out / "league" / "bowl-cap.db"
                dest.parent.mkdir(parents=True, exist_ok=True)
                info = backup.copy_sqlite_database(league_db, dest, checkpoint=False)
                info["slug"] = "bowl-cap"
                return [info]

            with patch.object(backup, "resolve_site_sqlite_path", return_value=site_db), patch.object(
                backup,
                "backup_league_databases",
                side_effect=_fake_league_backup,
            ), patch.object(backup, "league_slugs", return_value=["bowl-cap"]), patch.dict(
                "os.environ", {}, clear=True
            ):
                manifest = backup.run_backup(
                    out_dir,
                    checkpoint=False,
                    include_json=True,
                    instance_dir=root,
                )

            coverage = manifest["coverage"]["leagues"][0]
            self.assertTrue(coverage["ok"])
            self.assertEqual(coverage["categories"]["awards"]["total_rows"], 1)
            self.assertEqual(coverage["categories"]["hall_of_fame"]["total_rows"], 1)
            self.assertEqual(coverage["categories"]["records"]["total_rows"], 1)
            self.assertEqual(coverage["categories"]["archived_seasons"]["total_rows"], 1)

            history_dir = out_dir / "json" / "league" / "bowl-cap"
            awards = json.loads((history_dir / "awards.json").read_text(encoding="utf-8"))
            hof = json.loads((history_dir / "hall_of_fame.json").read_text(encoding="utf-8"))
            seasons = json.loads((history_dir / "seasons_index.json").read_text(encoding="utf-8"))
            self.assertEqual(awards["history_awards"][0]["award_name"], "MVP")
            self.assertEqual(hof[0]["player_name"], "Test Player")
            self.assertEqual(seasons[0]["label"], "2024-25")

            copied_join = out_dir / "admin_files" / "join_league" / "bowl-cap" / "available_teams.txt"
            copied_legacy = out_dir / "admin_files" / "join_league_available_teams.txt"
            self.assertTrue(copied_join.is_file())
            self.assertEqual(copied_join.read_text(encoding="utf-8"), "Alpha Team\n")
            self.assertTrue(copied_legacy.is_file())
            self.assertEqual(len(manifest["admin_files"]["copied"]), 2)

    def test_run_backup_no_json_still_inventory_and_admin_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_db = root / "site_membership.db"
            league_db = root / "bowl-cap.db"
            _create_site_db(site_db)
            _create_league_db(league_db)
            join_file = root / "join_league" / "bowl-cap" / "available_teams.txt"
            join_file.parent.mkdir(parents=True)
            join_file.write_text("Beta\n", encoding="utf-8")
            out_dir = root / "backup"

            def _fake_league_backup(out: Path, *, checkpoint: bool = True, verify: bool = False) -> list[dict]:
                dest = out / "league" / "bowl-cap.db"
                dest.parent.mkdir(parents=True, exist_ok=True)
                info = backup.copy_sqlite_database(league_db, dest, checkpoint=False)
                info["slug"] = "bowl-cap"
                return [info]

            with patch.object(backup, "resolve_site_sqlite_path", return_value=site_db), patch.object(
                backup,
                "backup_league_databases",
                side_effect=_fake_league_backup,
            ), patch.object(backup, "league_slugs", return_value=["bowl-cap"]), patch.dict(
                "os.environ", {}, clear=True
            ):
                manifest = backup.run_backup(
                    out_dir,
                    checkpoint=False,
                    include_json=False,
                    instance_dir=root,
                )

            self.assertNotIn("league_json", manifest)
            self.assertNotIn("site_json", manifest)
            self.assertFalse((out_dir / "json").exists())
            self.assertFalse((out_dir / "site" / "tables").exists())
            self.assertTrue(manifest["coverage"]["leagues"][0]["ok"])
            self.assertTrue(
                (out_dir / "admin_files" / "join_league" / "bowl-cap" / "available_teams.txt").is_file()
            )

    def test_prune_old_backups_keeps_newest_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "full_backups"
            root.mkdir()
            created: list[Path] = []
            for idx, name in enumerate(["a", "b", "c", "d", "e"]):
                folder = root / name
                folder.mkdir()
                (folder / "backup_manifest.json").write_text("{}\n", encoding="utf-8")
                # Distinct mtimes so newest ordering is stable.
                stamp = 1_700_000_000 + idx
                os.utime(folder, (stamp, stamp))
                created.append(folder)
            (root / "not-a-backup").mkdir()
            (root / "not-a-backup" / "readme.txt").write_text("x\n", encoding="utf-8")

            result = backup.prune_old_backups(root, keep=3)

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["retained"]), 3)
            self.assertEqual(len(result["removed"]), 2)
            self.assertTrue((root / "e").is_dir())
            self.assertTrue((root / "d").is_dir())
            self.assertTrue((root / "c").is_dir())
            self.assertFalse((root / "b").exists())
            self.assertFalse((root / "a").exists())
            self.assertTrue((root / "not-a-backup").is_dir())

    def test_retention_root_for_default_full_backups(self) -> None:
        root = backup.DEFAULT_BACKUP_ROOT
        self.assertEqual(
            backup.retention_root_for(root / "20260714T120000Z"),
            root.resolve(),
        )
        self.assertIsNone(backup.retention_root_for(Path("/tmp/custom-backup")))


if __name__ == "__main__":
    unittest.main()
