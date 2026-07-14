"""Tests for scripts/backup_all_live_data.py."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import backup_all_live_data as backup


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
            conn = sqlite3.connect(str(site_db))
            try:
                conn.execute(
                    """
                    CREATE TABLE ap_redemption_catalog (
                        id INTEGER PRIMARY KEY,
                        league_group TEXT NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        title TEXT NOT NULL,
                        description TEXT,
                        cost_ap INTEGER NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ap_redemption_catalog
                    (id, league_group, sort_order, title, description, cost_ap, is_active)
                    VALUES (1, 'bowl', 1, 'Test item', 'desc', 5, 1)
                    """
                )
                conn.commit()
            finally:
                conn.close()

            out_dir = root / "backup"
            with patch.object(backup, "resolve_site_sqlite_path", return_value=site_db), patch.object(
                backup,
                "backup_league_databases",
                return_value=[{"slug": "bowl-fantasy", "ok": False, "message": "source missing"}],
            ), patch.dict("os.environ", {}, clear=True):
                manifest = backup.run_backup(out_dir, checkpoint=False, include_json=True)

            self.assertTrue((out_dir / "backup_manifest.json").is_file())
            self.assertTrue(manifest["site"]["ok"])
            tables_dir = out_dir / "site" / "tables"
            catalog_path = tables_dir / "ap_redemption_catalog.json"
            self.assertTrue(catalog_path.is_file())
            rows = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "Test item")


if __name__ == "__main__":
    unittest.main()
