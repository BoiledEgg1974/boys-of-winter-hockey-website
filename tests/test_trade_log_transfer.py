"""Tests for trade log export/import between SQLite databases."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.trade_log_transfer import (
    export_trade_log_json,
    import_trade_log_json,
    merge_trade_log_sqlite,
)


def _make_db(path: Path, *, with_trade: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE teams (id INTEGER PRIMARY KEY, fhm_team_id TEXT, abbreviation TEXT, name TEXT)"
        )
        conn.execute("INSERT INTO teams (id, fhm_team_id, abbreviation, name) VALUES (1, '120', 'OAK', 'Oakland')")
        conn.execute("INSERT INTO teams (id, fhm_team_id, abbreviation, name) VALUES (2, '5', 'BOS', 'Boston')")
        conn.execute(
            """
            CREATE TABLE trade_log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE,
                team_a_id INTEGER NOT NULL,
                team_b_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                external_id TEXT,
                source TEXT NOT NULL
            )
            """
        )
        if with_trade:
            conn.execute(
                """
                INSERT INTO trade_log_entries
                    (trade_date, team_a_id, team_b_id, summary, source)
                VALUES ('2026-06-02', 1, 2, 'Oakland Seals sends:\nG Gary Simmons', 'manual')
                """
            )
        conn.commit()
    finally:
        conn.close()


class TradeLogTransferTests(unittest.TestCase):
    def test_export_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.db"
            dst = Path(tmp) / "dest.db"
            payload = Path(tmp) / "trades.json"
            _make_db(src, with_trade=True)
            _make_db(dst, with_trade=False)
            n = export_trade_log_json(src, payload)
            self.assertEqual(n, 1)
            ins, skip_ex, skip_un = import_trade_log_json(dst, payload)
            self.assertEqual((ins, skip_ex, skip_un), (1, 0, 0))
            conn = sqlite3.connect(str(dst))
            try:
                row = conn.execute(
                    "SELECT trade_date, team_a_id, team_b_id, source FROM trade_log_entries"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("2026-06-02", 1, 2, "manual"))

    def test_import_skips_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.db"
            payload = Path(tmp) / "trades.json"
            _make_db(db, with_trade=True)
            export_trade_log_json(db, payload)
            ins, skip_ex, skip_un = import_trade_log_json(db, payload)
            self.assertEqual((ins, skip_ex, skip_un), (0, 1, 0))

    def test_merge_sqlite_from_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "league2.db"
            primary = Path(tmp) / "bowl-historical.db"
            _make_db(legacy, with_trade=True)
            _make_db(primary, with_trade=False)
            ins, _, _ = merge_trade_log_sqlite(primary, legacy)
            self.assertEqual(ins, 1)


if __name__ == "__main__":
    unittest.main()
