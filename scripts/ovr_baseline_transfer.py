"""Export/import ``player_overall_baselines`` keyed by ``fhm_player_id``.

Used when uploading locally built league SQLite files to production: snapshot OVR on the
live server, download baselines, merge into the local DB, then upload so depth-chart arrows
compare live pre-update ratings to post-import ratings.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def export_ovr_baselines_json(db_path: Path, out_path: Path) -> int:
    """Write ``{fhm_player_id: baseline_score}`` from a league SQLite file."""
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_overall_baselines' LIMIT 1"
        ).fetchone()
        if not has_table:
            out_path.write_text("{}\n", encoding="utf-8")
            return 0
        rows = conn.execute(
            """
            SELECT p.fhm_player_id, b.baseline_score
            FROM player_overall_baselines b
            JOIN players p ON p.id = b.player_id
            WHERE p.fhm_player_id IS NOT NULL AND TRIM(p.fhm_player_id) != ''
            """
        ).fetchall()
    finally:
        conn.close()
    data = {str(fhm): int(score) for fhm, score in rows if fhm is not None}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(data)


def import_ovr_baselines_json(db_path: Path, in_path: Path) -> tuple[int, int]:
    """Merge baseline scores into ``db_path`` by ``fhm_player_id``. Returns (written, skipped)."""
    db_path = db_path.resolve()
    in_path = in_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not in_path.is_file():
        raise FileNotFoundError(in_path)
    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in {in_path}")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    now = _utc_now_iso()
    written = 0
    skipped = 0
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_overall_baselines (
                player_id INTEGER NOT NULL PRIMARY KEY,
                baseline_score INTEGER NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(player_id) REFERENCES players (id)
            )
            """
        )
        id_by_fhm = {
            str(fhm): int(pid)
            for fhm, pid in conn.execute(
                "SELECT fhm_player_id, id FROM players WHERE fhm_player_id IS NOT NULL AND TRIM(fhm_player_id) != ''"
            )
            if fhm is not None
        }
        for fhm, score in raw.items():
            pid = id_by_fhm.get(str(fhm))
            if pid is None:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO player_overall_baselines (player_id, baseline_score, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    baseline_score = excluded.baseline_score,
                    updated_at = excluded.updated_at
                """,
                (pid, int(score), now),
            )
            written += 1
        conn.commit()
    finally:
        conn.close()
    return written, skipped


def _resolve_db_path(slug: str | None, db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path.resolve()
    if not slug:
        raise SystemExit("Provide --slug or --db-path.")
    from app.config import resolve_league_sqlite_path

    return resolve_league_sqlite_path(slug).resolve()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export baselines from a league DB to JSON.")
    p_export.add_argument("--slug", help="League slug (e.g. bowl-historical).")
    p_export.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_export.add_argument("--out", type=Path, required=True, help="Output JSON path.")

    p_import = sub.add_parser("import", help="Import baselines from JSON into a league DB.")
    p_import.add_argument("--slug", help="League slug (e.g. bowl-historical).")
    p_import.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_import.add_argument("--in", dest="in_path", type=Path, required=True, help="Input JSON path.")

    args = p.parse_args()
    if args.command == "export":
        db_path = _resolve_db_path(args.slug, args.db_path)
        n = export_ovr_baselines_json(db_path, args.out)
        print(f"export_ovr_baselines: {db_path.name} -> {args.out} ({n} players)")
        return 0
    if args.command == "import":
        db_path = _resolve_db_path(args.slug, args.db_path)
        written, skipped = import_ovr_baselines_json(db_path, args.in_path)
        print(
            f"import_ovr_baselines: {args.in_path.name} -> {db_path.name} "
            f"({written} written, {skipped} fhm ids not in target DB)"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
