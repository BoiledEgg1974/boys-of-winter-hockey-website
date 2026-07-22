"""Tests for scripts/league_editorial_transfer.py."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import league_editorial_transfer as transfer


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE players (id INTEGER PRIMARY KEY, fhm_player_id TEXT);
            CREATE TABLE teams (id INTEGER PRIMARY KEY, fhm_team_id TEXT);
            CREATE TABLE seasons (
                id INTEGER PRIMARY KEY, label TEXT, start_year INTEGER, is_current INTEGER
            );
            INSERT INTO players VALUES (1, 'P1'), (2, 'P2');
            INSERT INTO teams VALUES (10, 'T10'), (20, 'T20');
            INSERT INTO seasons VALUES (100, '1999-00', 1999, 1);
            """
        )
        conn.commit()
    finally:
        conn.close()
    # Ensure editorial DDL via ensure helper through import of empty bundle
    empty = path.parent / "empty.json"
    empty.write_text(json.dumps({"version": 1}) + "\n", encoding="utf-8")
    transfer.import_league_editorial_json(path, empty)


class LeagueEditorialTransferTests(unittest.TestCase):
    def test_honors_and_adjustments_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    "INSERT INTO team_honors_meta (team_id, retired_section_enabled) VALUES (10, 1)"
                )
                conn.execute(
                    """
                    INSERT INTO team_retired_numbers (
                        team_id, player_name, jersey_number, jersey_image_rel_path, number_color,
                        is_active, sort_order, notes, created_at, updated_at
                    ) VALUES (10, 'Legend', 99, 'img/99.png', '#fff', 1, 0, '', '2020-01-01', '2020-01-01')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO record_stat_adjustments (
                        adj_type, line_kind, player_id, season_year, team_fhm_id,
                        career_source, overrides_json, notes, updated_at
                    ) VALUES ('exclude', 'skater_career', 1, 1999, 'T10', 'rs', NULL, 'note', '2020-01-01')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 2005, 1, 'admin', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            counts = transfer.export_league_editorial_json(live, out)
            self.assertGreaterEqual(counts["team_retired_numbers"], 1)
            self.assertGreaterEqual(counts["record_stat_adjustments"], 1)
            self.assertGreaterEqual(counts["hall_of_fame_members"], 1)

            written = transfer.import_league_editorial_json(local, out)
            self.assertGreaterEqual(written["team_honors"], 1)
            self.assertGreaterEqual(written["record_stat_adjustments"], 1)
            self.assertGreaterEqual(written["hall_of_fame_members"], 1)

            c = sqlite3.connect(str(local))
            try:
                self.assertEqual(
                    c.execute(
                        "SELECT retired_section_enabled FROM team_honors_meta WHERE team_id=10"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT player_name, jersey_number FROM team_retired_numbers WHERE team_id=10"
                    ).fetchone(),
                    ("Legend", 99),
                )
                self.assertEqual(
                    c.execute(
                        "SELECT adj_type, player_id FROM record_stat_adjustments"
                    ).fetchone(),
                    ("exclude", 1),
                )
                self.assertEqual(
                    c.execute(
                        "SELECT inducted_year, source FROM hall_of_fame_members WHERE player_id=1"
                    ).fetchone(),
                    (2005, "admin"),
                )
            finally:
                c.close()

    def test_hof_admin_overrides_local_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 2010, 2, 'admin', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 2000, 1, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            transfer.import_league_editorial_json(local, out)
            c = sqlite3.connect(str(local))
            try:
                row = c.execute(
                    "SELECT inducted_year, source FROM hall_of_fame_members WHERE player_id=1"
                ).fetchone()
                self.assertEqual(row, (2010, "admin"))
            finally:
                c.close()

    def test_hof_gap_fill_restores_missing_and_keeps_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 1969, 0, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (2, 'goalie', 1968, 1, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            transfer.import_league_editorial_json(local, out)
            c = sqlite3.connect(str(local))
            try:
                rows = {
                    int(r[0]): (int(r[1]), r[2])
                    for r in c.execute(
                        "SELECT player_id, inducted_year, source FROM hall_of_fame_members"
                    )
                }
                self.assertEqual(rows[1], (1969, "csv"))
                self.assertEqual(rows[2], (1968, "csv"))
            finally:
                c.close()

    def test_hof_csv_gap_fill_does_not_overwrite_local_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 1950, 0, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 1969, 5, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            transfer.import_league_editorial_json(local, out)
            c = sqlite3.connect(str(local))
            try:
                row = c.execute(
                    "SELECT inducted_year, sort_order, source FROM hall_of_fame_members WHERE player_id=1"
                ).fetchone()
                self.assertEqual(row, (1969, 5, "csv"))
            finally:
                c.close()

    def test_history_awards_csv_gap_fill_and_admin_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO history_awards (
                        season_id, award_name, player_id, team_id, staff_fhm_id,
                        notes, source, updated_at
                    ) VALUES
                      (100, 'Hart', 1, 10, NULL, NULL, 'csv', '2020-01-01'),
                      (100, 'Norris', 2, 20, NULL, 'admin note', 'admin', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute(
                    """
                    INSERT INTO history_awards (
                        season_id, award_name, player_id, team_id, staff_fhm_id,
                        notes, source, updated_at
                    ) VALUES (100, 'Norris', 2, 20, NULL, 'local csv', 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            written = transfer.import_league_editorial_json(local, out)
            self.assertGreaterEqual(written["history_awards"], 2)
            c = sqlite3.connect(str(local))
            try:
                by_name = {
                    r[0]: (int(r[1]), r[2], r[3])
                    for r in c.execute(
                        "SELECT award_name, player_id, source, notes FROM history_awards"
                    )
                }
                # Live-only CSV Hart restored.
                self.assertEqual(by_name["Hart"][:2], (1, "csv"))
                # Live admin Norris overwrote local CSV.
                self.assertEqual(by_name["Norris"], (2, "admin", "admin note"))
            finally:
                c.close()

    def test_history_awards_gap_fill_keeps_matching_local_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO history_awards (
                        season_id, award_name, player_id, team_id, staff_fhm_id,
                        notes, source, updated_at
                    ) VALUES (100, 'Hart', 1, 10, NULL, 'live', 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute(
                    """
                    INSERT INTO history_awards (
                        season_id, award_name, player_id, team_id, staff_fhm_id,
                        notes, source, updated_at
                    ) VALUES (100, 'Hart', 1, 10, NULL, 'local corrected', 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            transfer.import_league_editorial_json(local, out)
            c = sqlite3.connect(str(local))
            try:
                row = c.execute(
                    "SELECT notes, source FROM history_awards WHERE award_name='Hart'"
                ).fetchone()
                self.assertEqual(row, ("local corrected", "csv"))
            finally:
                c.close()

    def test_export_includes_total_rows_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            out = root / "bundle.json"
            _create_db(live)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO hall_of_fame_members (
                        player_id, member_kind, inducted_year, sort_order, source, updated_at
                    ) VALUES (1, 'skater', 1969, 0, 'csv', '2020-01-01')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            counts = transfer.export_league_editorial_json(live, out)
            raw = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], transfer.BUNDLE_VERSION)
            self.assertIn("total_rows", raw)
            self.assertEqual(raw["total_rows"], sum(counts.values()))
            self.assertEqual(raw["row_counts"]["hall_of_fame_members"], 1)
    def test_player_boost_tiers_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute("ALTER TABLE players ADD COLUMN boost_tier TEXT NOT NULL DEFAULT ''")
                conn.execute("UPDATE players SET boost_tier='gold' WHERE id=1")
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(str(local))
            try:
                conn.execute("ALTER TABLE players ADD COLUMN boost_tier TEXT NOT NULL DEFAULT ''")
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            written = transfer.import_league_editorial_json(local, out)
            self.assertEqual(written["player_boost_tiers"], 1)
            c = sqlite3.connect(str(local))
            try:
                self.assertEqual(
                    c.execute("SELECT boost_tier FROM players WHERE id=1").fetchone()[0],
                    "gold",
                )
            finally:
                c.close()

    def test_franchise_identity_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.db"
            local = root / "local.db"
            out = root / "bundle.json"
            _create_db(live)
            _create_db(local)
            conn = sqlite3.connect(str(live))
            try:
                conn.execute(
                    """
                    INSERT INTO franchise_team_identities (
                        team_id, team_fhm_id, display_name, abbreviation, logo_file,
                        start_year, end_year, status, notes
                    ) VALUES (10, 'T10', 'Old Name', 'OLD', 'old.png', 1980, 1989, 'historical', 'admin')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            transfer.export_league_editorial_json(live, out)
            transfer.import_league_editorial_json(local, out)
            c = sqlite3.connect(str(local))
            try:
                row = c.execute(
                    "SELECT display_name, logo_file, notes FROM franchise_team_identities"
                ).fetchone()
                self.assertEqual(row, ("Old Name", "old.png", "admin"))
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
