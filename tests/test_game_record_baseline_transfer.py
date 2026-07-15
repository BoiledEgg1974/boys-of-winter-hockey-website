"""Tests for scripts/game_record_baseline_transfer.py."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import game_record_baseline_transfer as transfer


def _create_league_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                fhm_player_id TEXT
            );
            CREATE TABLE teams (
                id INTEGER PRIMARY KEY,
                fhm_team_id TEXT
            );
            CREATE TABLE game_record_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_key VARCHAR(64) NOT NULL,
                segment VARCHAR(8) NOT NULL DEFAULT 'rs',
                scope VARCHAR(16) NOT NULL DEFAULT 'all',
                player_kind VARCHAR(16) NOT NULL DEFAULT 'skater',
                value FLOAT NOT NULL,
                player_id INTEGER,
                team_id INTEGER,
                opponent_team_id INTEGER,
                game_id INTEGER,
                game_date DATE,
                season_label VARCHAR(32),
                notes TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE (metric_key, segment, scope, player_kind)
            );
            INSERT INTO players (id, fhm_player_id) VALUES
                (1, 'P1'), (2, 'P2'), (3, 'P3');
            INSERT INTO teams (id, fhm_team_id) VALUES
                (10, 'T10'), (20, 'T20'), (30, 'T30');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_baseline(
    path: Path,
    *,
    metric_key: str,
    value: float,
    player_id: int | None = 1,
    team_id: int | None = 10,
    opponent_team_id: int | None = 20,
    season_label: str = "1999-00",
    notes: str | None = "admin-manual",
    game_date: str = "1999-11-01",
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT INTO game_record_baselines (
                metric_key, segment, scope, player_kind, value,
                player_id, team_id, opponent_team_id, game_id,
                game_date, season_label, notes, created_at, updated_at
            ) VALUES (?, 'rs', 'all', 'skater', ?, ?, ?, ?, NULL, ?, ?, ?, '2020-01-01', '2020-01-01')
            """,
            (metric_key, value, player_id, team_id, opponent_team_id, game_date, season_label, notes),
        )
        conn.commit()
    finally:
        conn.close()


def _get_baseline(path: Path, metric_key: str) -> tuple:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            """
            SELECT value, player_id, team_id, opponent_team_id, season_label, notes, game_date
            FROM game_record_baselines
            WHERE metric_key=? AND segment='rs' AND scope='all' AND player_kind='skater'
            """,
            (metric_key,),
        ).fetchone()
    finally:
        conn.close()


class GameRecordBaselineTransferTests(unittest.TestCase):
    def test_export_uses_fhm_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "league.db"
            out = root / "out.json"
            _create_league_db(db)
            _insert_baseline(db, metric_key="goals", value=5.0)
            n = transfer.export_game_record_baselines_json(db, out)
            self.assertEqual(n, 1)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            row = payload[0]
            self.assertEqual(row["metric_key"], "goals")
            self.assertEqual(row["value"], 5.0)
            self.assertEqual(row["player_fhm_id"], "P1")
            self.assertEqual(row["team_fhm_id"], "T10")
            self.assertEqual(row["opponent_fhm_id"], "T20")
            self.assertEqual(row["notes"], "admin-manual")
            self.assertNotIn("game_id", row)

    def test_import_live_fills_empty_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            json_path = root / "live.json"
            _create_league_db(live)
            _create_league_db(local)
            _insert_baseline(live, metric_key="goals", value=5.0, season_label="1995-96")
            transfer.export_game_record_baselines_json(live, json_path)
            written, skipped, kept = transfer.import_game_record_baselines_json(local, json_path)
            self.assertEqual(written, 1)
            self.assertEqual(kept, 0)
            row = _get_baseline(local, "goals")
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 5.0)
            self.assertEqual(row[1], 1)
            self.assertEqual(row[4], "1995-96")
            self.assertEqual(row[5], "admin-manual")

    def test_import_live_wins_equal_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            json_path = root / "live.json"
            _create_league_db(live)
            _create_league_db(local)
            _insert_baseline(
                live,
                metric_key="goals",
                value=3.0,
                player_id=2,
                team_id=20,
                season_label="1988-89",
                notes="admin-manual",
                game_date="1988-12-12",
            )
            _insert_baseline(
                local,
                metric_key="goals",
                value=3.0,
                player_id=3,
                team_id=30,
                season_label="2000-01",
                notes=None,
                game_date="2000-10-04",
            )
            transfer.export_game_record_baselines_json(live, json_path)
            written, skipped, kept = transfer.import_game_record_baselines_json(local, json_path)
            self.assertEqual(written, 1)
            self.assertEqual(kept, 0)
            row = _get_baseline(local, "goals")
            self.assertEqual(row[0], 3.0)
            self.assertEqual(row[1], 2)  # remapped from live P2
            self.assertEqual(row[4], "1988-89")
            self.assertEqual(row[5], "admin-manual")
            self.assertEqual(row[6], "1988-12-12")

    def test_import_keeps_local_better_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            json_path = root / "live.json"
            _create_league_db(live)
            _create_league_db(local)
            _insert_baseline(live, metric_key="goals", value=3.0, season_label="1988-89")
            _insert_baseline(
                local,
                metric_key="goals",
                value=5.0,
                player_id=3,
                season_label="2000-01",
                notes=None,
            )
            transfer.export_game_record_baselines_json(live, json_path)
            written, skipped, kept = transfer.import_game_record_baselines_json(local, json_path)
            self.assertEqual(written, 0)
            self.assertEqual(kept, 1)
            row = _get_baseline(local, "goals")
            self.assertEqual(row[0], 5.0)
            self.assertEqual(row[4], "2000-01")

    def test_import_live_better_overwrites_weaker_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            json_path = root / "live.json"
            _create_league_db(live)
            _create_league_db(local)
            _insert_baseline(live, metric_key="goals", value=7.0, season_label="1991-92")
            _insert_baseline(
                local,
                metric_key="goals",
                value=3.0,
                season_label="2000-01",
                notes=None,
            )
            transfer.export_game_record_baselines_json(live, json_path)
            written, skipped, kept = transfer.import_game_record_baselines_json(local, json_path)
            self.assertEqual(written, 1)
            self.assertEqual(kept, 0)
            row = _get_baseline(local, "goals")
            self.assertEqual(row[0], 7.0)
            self.assertEqual(row[4], "1991-92")

    def test_plus_minus_low_prefers_lower_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            json_path = root / "live.json"
            _create_league_db(live)
            _create_league_db(local)
            _insert_baseline(live, metric_key="plus_minus_low", value=-6.0, season_label="1990-91")
            _insert_baseline(
                local,
                metric_key="plus_minus_low",
                value=-4.0,
                season_label="2000-01",
                notes=None,
            )
            transfer.export_game_record_baselines_json(live, json_path)
            written, skipped, kept = transfer.import_game_record_baselines_json(local, json_path)
            self.assertEqual(written, 1)
            row = _get_baseline(local, "plus_minus_low")
            self.assertEqual(row[0], -6.0)
            self.assertEqual(row[4], "1990-91")


if __name__ == "__main__":
    unittest.main()
