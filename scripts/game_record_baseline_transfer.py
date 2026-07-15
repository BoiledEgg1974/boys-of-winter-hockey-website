"""Export/import ``game_record_baselines`` keyed by metric+segment+scope+player_kind.

Used when uploading locally built league SQLite files to production: capture live
single-game record baselines from the server, merge into the local DB (keeping
strictly better marks; live wins ties), then upload so admin-seeded Cap/Historical
records are not wiped by a local DB replace.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Only plus_minus_low is lower-is-better among game-record metrics.
_LOWER_IS_BETTER_KEYS = frozenset({"plus_minus_low"})

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS game_record_baselines (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
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
    FOREIGN KEY(player_id) REFERENCES players (id),
    FOREIGN KEY(team_id) REFERENCES teams (id),
    FOREIGN KEY(opponent_team_id) REFERENCES teams (id),
    FOREIGN KEY(game_id) REFERENCES games (id),
    UNIQUE (metric_key, segment, scope, player_kind)
)
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _higher_is_better(metric_key: str) -> bool:
    return str(metric_key or "") not in _LOWER_IS_BETTER_KEYS


def _is_better(a: float, b: float, *, higher_is_better: bool) -> bool:
    if higher_is_better:
        return a > b
    return a < b


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("metric_key") or ""),
        str(row.get("segment") or "rs"),
        str(row.get("scope") or "all"),
        str(row.get("player_kind") or "skater"),
    )


def _fhm_lookups(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[int, str], dict[str, int], dict[int, str]]:
    player_id_by_fhm: dict[str, int] = {}
    player_fhm_by_id: dict[int, str] = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='players' LIMIT 1"
    ).fetchone():
        for pid, fhm in conn.execute(
            "SELECT id, fhm_player_id FROM players "
            "WHERE fhm_player_id IS NOT NULL AND TRIM(fhm_player_id) != ''"
        ):
            key = str(fhm).strip()
            player_id_by_fhm[key] = int(pid)
            player_fhm_by_id[int(pid)] = key

    team_id_by_fhm: dict[str, int] = {}
    team_fhm_by_id: dict[int, str] = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams' LIMIT 1"
    ).fetchone():
        for tid, fhm in conn.execute(
            "SELECT id, fhm_team_id FROM teams "
            "WHERE fhm_team_id IS NOT NULL AND TRIM(fhm_team_id) != ''"
        ):
            key = str(fhm).strip()
            team_id_by_fhm[key] = int(tid)
            team_fhm_by_id[int(tid)] = key
    return player_id_by_fhm, player_fhm_by_id, team_id_by_fhm, team_fhm_by_id


def export_game_record_baselines_json(db_path: Path, out_path: Path) -> int:
    """Write portable game_record_baselines rows from ``db_path`` to JSON."""
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_record_baselines' LIMIT 1"
        ).fetchone():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("[]\n", encoding="utf-8")
            return 0
        _player_id_by_fhm, player_fhm_by_id, _team_id_by_fhm, team_fhm_by_id = _fhm_lookups(conn)
        rows = conn.execute(
            """
            SELECT metric_key, segment, scope, player_kind, value,
                   player_id, team_id, opponent_team_id,
                   game_date, season_label, notes
            FROM game_record_baselines
            ORDER BY segment, scope, player_kind, metric_key
            """
        ).fetchall()
    finally:
        conn.close()

    payload: list[dict[str, Any]] = []
    for (
        metric_key,
        segment,
        scope,
        player_kind,
        value,
        player_id,
        team_id,
        opponent_team_id,
        game_date,
        season_label,
        notes,
    ) in rows:
        payload.append(
            {
                "metric_key": metric_key,
                "segment": segment,
                "scope": scope,
                "player_kind": player_kind,
                "value": float(value),
                "player_fhm_id": player_fhm_by_id.get(int(player_id)) if player_id is not None else None,
                "team_fhm_id": team_fhm_by_id.get(int(team_id)) if team_id is not None else None,
                "opponent_fhm_id": (
                    team_fhm_by_id.get(int(opponent_team_id)) if opponent_team_id is not None else None
                ),
                "game_date": str(game_date) if game_date is not None else None,
                "season_label": season_label,
                "notes": notes,
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(payload)


def _should_take_incoming(local_value: float | None, incoming_value: float, metric_key: str) -> bool:
    """True when incoming (live) should replace or create the local row."""
    if local_value is None:
        return True
    hib = _higher_is_better(metric_key)
    if _is_better(incoming_value, float(local_value), higher_is_better=hib):
        return True
    if _is_better(float(local_value), incoming_value, higher_is_better=hib):
        return False
    # Equal values: prefer live (incoming) so admin holder/date/notes survive.
    return True


def import_game_record_baselines_json(db_path: Path, in_path: Path) -> tuple[int, int, int]:
    """Merge live JSON baselines into ``db_path``.

    Returns ``(written, skipped_identity, kept_local_better)``.
    """
    db_path = db_path.resolve()
    in_path = in_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not in_path.is_file():
        raise FileNotFoundError(in_path)
    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array in {in_path}")

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    written = 0
    skipped = 0
    kept_local = 0
    now = _utc_now_iso()
    try:
        conn.execute(_TABLE_DDL)
        has_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='ix_game_record_baseline_segment' LIMIT 1"
        ).fetchone()
        if not has_index:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_game_record_baseline_segment "
                "ON game_record_baselines (segment, scope, player_kind)"
            )

        player_id_by_fhm, _pf, team_id_by_fhm, _tf = _fhm_lookups(conn)
        existing = {
            (str(r[0]), str(r[1]), str(r[2]), str(r[3])): float(r[4])
            for r in conn.execute(
                "SELECT metric_key, segment, scope, player_kind, value FROM game_record_baselines"
            )
        }

        for item in raw:
            if not isinstance(item, dict):
                skipped += 1
                continue
            metric_key = str(item.get("metric_key") or "").strip()
            segment = str(item.get("segment") or "rs").strip() or "rs"
            scope = str(item.get("scope") or "all").strip() or "all"
            player_kind = str(item.get("player_kind") or "skater").strip() or "skater"
            if not metric_key:
                skipped += 1
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                skipped += 1
                continue

            key = (metric_key, segment, scope, player_kind)
            local_val = existing.get(key)
            if not _should_take_incoming(local_val, value, metric_key):
                kept_local += 1
                continue

            player_fhm = str(item.get("player_fhm_id") or "").strip() or None
            team_fhm = str(item.get("team_fhm_id") or "").strip() or None
            opp_fhm = str(item.get("opponent_fhm_id") or "").strip() or None
            player_id = player_id_by_fhm.get(player_fhm) if player_fhm else None
            team_id = team_id_by_fhm.get(team_fhm) if team_fhm else None
            opponent_id = team_id_by_fhm.get(opp_fhm) if opp_fhm else None
            # Player/team FHM missing locally: still store the mark (value matters);
            # identity columns may be null. Count unresolved FHMs for deploy logs.
            if (player_fhm and player_id is None) or (team_fhm and team_id is None) or (
                opp_fhm and opponent_id is None
            ):
                skipped += 1
            game_date = item.get("game_date")
            season_label = item.get("season_label")
            notes = item.get("notes")

            conn.execute(
                """
                INSERT INTO game_record_baselines (
                    metric_key, segment, scope, player_kind, value,
                    player_id, team_id, opponent_team_id, game_id,
                    game_date, season_label, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_key, segment, scope, player_kind) DO UPDATE SET
                    value = excluded.value,
                    player_id = excluded.player_id,
                    team_id = excluded.team_id,
                    opponent_team_id = excluded.opponent_team_id,
                    game_id = NULL,
                    game_date = excluded.game_date,
                    season_label = excluded.season_label,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    metric_key,
                    segment,
                    scope,
                    player_kind,
                    value,
                    player_id,
                    team_id,
                    opponent_id,
                    game_date,
                    season_label,
                    notes,
                    now,
                    now,
                ),
            )
            existing[key] = value
            written += 1
        conn.commit()
    finally:
        conn.close()
    return written, skipped, kept_local


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

    p_export = sub.add_parser("export", help="Export game record baselines from a league DB to JSON.")
    p_export.add_argument("--slug", help="League slug (e.g. bowl-cap).")
    p_export.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_export.add_argument("--out", type=Path, required=True, help="Output JSON path.")

    p_import = sub.add_parser(
        "import",
        help="Merge live game record baselines from JSON into a league DB.",
    )
    p_import.add_argument("--slug", help="League slug (e.g. bowl-cap).")
    p_import.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_import.add_argument("--in", dest="in_path", type=Path, required=True, help="Input JSON path.")

    args = p.parse_args()
    if args.command == "export":
        db_path = _resolve_db_path(args.slug, args.db_path)
        n = export_game_record_baselines_json(db_path, args.out)
        print(f"export_game_record_baselines: {db_path.name} -> {args.out} ({n} rows)")
        return 0
    if args.command == "import":
        db_path = _resolve_db_path(args.slug, args.db_path)
        written, skipped, kept = import_game_record_baselines_json(db_path, args.in_path)
        print(
            f"import_game_record_baselines: {args.in_path.name} -> {db_path.name} "
            f"({written} written, {skipped} unresolved identities, {kept} kept local-better)"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
