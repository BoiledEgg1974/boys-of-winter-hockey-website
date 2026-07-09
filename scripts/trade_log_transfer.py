"""Export/import manual trade log rows between league SQLite databases.

Used when uploading locally built league DBs to production (preserve live manual
entries) and when recovering trades from legacy/backup SQLite files.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PUBLIC_SOURCES = ("manual", "csv")
_LEGACY_LEAGUE_DB_FILES: dict[str, str] = {
    "bowl-historical": "league2.db",
    "bowl-fantasy": "bow.db",
    "bowl-cap": "league3.db",
}
_SQLITE_HEADER = b"SQLite format 3\x00"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not _sqlite_file_is_readable(db_path):
        raise sqlite3.DatabaseError(f"file is not a database: {db_path}")
    return sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30.0)


def _connect_rw(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path.resolve()), timeout=30.0)


def _sqlite_file_is_readable(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 100:
            return False
        with open(path, "rb") as header:
            if header.read(16) != _SQLITE_HEADER:
                return False
    except OSError:
        return False
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(path), timeout=30.0)
        except sqlite3.Error:
            return False
    try:
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _sqlite_has_league_content(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(path), timeout=30.0)
        except sqlite3.Error:
            return False
    try:
        for table in ("teams", "players", "games", "seasons"):
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (table,),
            ).fetchone():
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n and int(n) > 0:
                return True
        return False
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def resolve_league_sqlite_path(slug: str) -> Path:
    """Standalone copy of app.config.resolve_league_sqlite_path (no Flask imports)."""
    inst = ROOT / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    primary = inst / f"{slug}.db"
    legacy_name = _LEGACY_LEAGUE_DB_FILES.get(slug)
    legacy = inst / legacy_name if legacy_name else None

    prim_exists = primary.is_file()
    leg_exists = legacy.is_file() if legacy else False
    prim_valid = prim_exists and _sqlite_file_is_readable(primary)
    leg_valid = leg_exists and legacy is not None and _sqlite_file_is_readable(legacy)
    prim_populated = prim_valid and _sqlite_has_league_content(primary)
    leg_populated = leg_valid and legacy is not None and _sqlite_has_league_content(legacy)

    if prim_populated:
        return primary
    if leg_populated:
        return legacy
    if leg_exists and not prim_populated and leg_valid:
        return legacy
    if prim_exists and not prim_valid and leg_valid and legacy is not None:
        return legacy
    if prim_valid:
        return primary
    if leg_valid and legacy is not None:
        return legacy
    if prim_exists:
        return primary
    if leg_exists and legacy is not None:
        return legacy
    return primary


def _team_lookup(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    by_fhm: dict[str, int] = {}
    by_abbr: dict[str, int] = {}
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams' LIMIT 1"
    ).fetchone():
        return by_fhm, by_abbr
    for tid, fhm, abbr in conn.execute(
        "SELECT id, fhm_team_id, abbreviation FROM teams"
    ):
        if fhm is not None and str(fhm).strip() != "":
            by_fhm[str(fhm).strip()] = int(tid)
        if abbr is not None and str(abbr).strip():
            by_abbr[str(abbr).strip().upper()] = int(tid)
    return by_fhm, by_abbr


def _resolve_team_id(
    *,
    team_id: int | None,
    team_fhm: str | None,
    team_abbr: str | None,
    by_fhm: dict[str, int],
    by_abbr: dict[str, int],
) -> int | None:
    if team_fhm and str(team_fhm).strip() in by_fhm:
        return by_fhm[str(team_fhm).strip()]
    if team_abbr and str(team_abbr).strip().upper() in by_abbr:
        return by_abbr[str(team_abbr).strip().upper()]
    if team_id is not None:
        return int(team_id)
    return None


def _trade_key(row: dict) -> tuple:
    td = str(row.get("trade_date") or "")
    a = str(row.get("team_a_fhm") or row.get("team_a_abbr") or row.get("team_a_id") or "")
    b = str(row.get("team_b_fhm") or row.get("team_b_abbr") or row.get("team_b_id") or "")
    pair = tuple(sorted((a, b)))
    summary = " ".join(str(row.get("summary") or "").split())
    return td, pair, summary


def export_trade_log_json(db_path: Path, out_path: Path, *, sources: tuple[str, ...] = _PUBLIC_SOURCES) -> int:
    """Write trade log rows from ``db_path`` to JSON."""
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    conn = _connect_ro(db_path)
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_log_entries' LIMIT 1"
        ).fetchone():
            out_path.write_text("[]\n", encoding="utf-8")
            return 0
        by_fhm, by_abbr = _team_lookup(conn)
        fhm_by_id = {v: k for k, v in by_fhm.items()}
        abbr_by_id = {v: k for k, v in by_abbr.items()}
        placeholders = ",".join("?" for _ in sources)
        rows = conn.execute(
            f"""
            SELECT id, trade_date, team_a_id, team_b_id, summary, external_id, source
            FROM trade_log_entries
            WHERE source IN ({placeholders})
            ORDER BY trade_date DESC NULLS LAST, id DESC
            """,
            sources,
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for rid, trade_date, ta, tb, summary, external_id, source in rows:
        out.append(
            {
                "id": int(rid),
                "trade_date": trade_date if trade_date is None else str(trade_date),
                "team_a_id": int(ta),
                "team_b_id": int(tb),
                "team_a_fhm": fhm_by_id.get(int(ta)),
                "team_b_fhm": fhm_by_id.get(int(tb)),
                "team_a_abbr": abbr_by_id.get(int(ta)),
                "team_b_abbr": abbr_by_id.get(int(tb)),
                "summary": str(summary or ""),
                "external_id": external_id,
                "source": str(source or "manual"),
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return len(out)


def import_trade_log_json(
    db_path: Path,
    in_path: Path,
    *,
    prefer_source: str | None = None,
) -> tuple[int, int, int]:
    """Merge trade rows into ``db_path``. Returns (inserted, skipped_existing, skipped_unresolved)."""
    db_path = db_path.resolve()
    in_path = in_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not in_path.is_file():
        raise FileNotFoundError(in_path)
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON array in {in_path}")

    conn = _connect_rw(db_path)
    inserted = skipped_existing = skipped_unresolved = 0
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_log_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE,
                team_a_id INTEGER NOT NULL,
                team_b_id INTEGER NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                external_id VARCHAR(64),
                source VARCHAR(16) NOT NULL DEFAULT 'csv',
                FOREIGN KEY(team_a_id) REFERENCES teams (id),
                FOREIGN KEY(team_b_id) REFERENCES teams (id)
            )
            """
        )
        by_fhm, by_abbr = _team_lookup(conn)
        fhm_by_id = {v: k for k, v in by_fhm.items()}
        abbr_by_id = {v: k for k, v in by_abbr.items()}
        existing_keys: set[tuple] = set()
        existing_external: set[str] = set()
        for td, ta, tb, summary, ext in conn.execute(
            "SELECT trade_date, team_a_id, team_b_id, summary, external_id FROM trade_log_entries"
        ):
            row = {
                "trade_date": str(td) if td is not None else None,
                "team_a_fhm": fhm_by_id.get(int(ta)),
                "team_b_fhm": fhm_by_id.get(int(tb)),
                "team_a_abbr": abbr_by_id.get(int(ta)),
                "team_b_abbr": abbr_by_id.get(int(tb)),
                "team_a_id": int(ta),
                "team_b_id": int(tb),
                "summary": str(summary or ""),
            }
            existing_keys.add(_trade_key(row))
            if ext:
                existing_external.add(str(ext).strip())

        for raw in payload:
            if not isinstance(raw, dict):
                continue
            ext = (raw.get("external_id") or "").strip() or None
            if ext and ext in existing_external:
                skipped_existing += 1
                continue
            ta = _resolve_team_id(
                team_id=raw.get("team_a_id"),
                team_fhm=raw.get("team_a_fhm"),
                team_abbr=raw.get("team_a_abbr"),
                by_fhm=by_fhm,
                by_abbr=by_abbr,
            )
            tb = _resolve_team_id(
                team_id=raw.get("team_b_id"),
                team_fhm=raw.get("team_b_fhm"),
                team_abbr=raw.get("team_b_abbr"),
                by_fhm=by_fhm,
                by_abbr=by_abbr,
            )
            if ta is None or tb is None:
                skipped_unresolved += 1
                continue
            key_row = {
                "trade_date": raw.get("trade_date"),
                "team_a_fhm": raw.get("team_a_fhm"),
                "team_b_fhm": raw.get("team_b_fhm"),
                "team_a_abbr": raw.get("team_a_abbr"),
                "team_b_abbr": raw.get("team_b_abbr"),
                "team_a_id": ta,
                "team_b_id": tb,
                "summary": raw.get("summary") or "",
            }
            if _trade_key(key_row) in existing_keys:
                skipped_existing += 1
                continue
            trade_date = raw.get("trade_date")
            if trade_date:
                try:
                    trade_date = date.fromisoformat(str(trade_date))
                except ValueError:
                    trade_date = None
            else:
                trade_date = None
            source = str(prefer_source or raw.get("source") or "manual").strip() or "manual"
            conn.execute(
                """
                INSERT INTO trade_log_entries
                    (trade_date, team_a_id, team_b_id, summary, external_id, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    int(ta),
                    int(tb),
                    str(raw.get("summary") or ""),
                    ext,
                    source,
                ),
            )
            existing_keys.add(_trade_key(key_row))
            if ext:
                existing_external.add(ext)
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted, skipped_existing, skipped_unresolved


def merge_trade_log_sqlite(
    target_db: Path,
    source_db: Path,
    *,
    sources: tuple[str, ...] = _PUBLIC_SOURCES,
) -> tuple[int, int, int]:
    """Export from ``source_db`` to a temp JSON file and import into ``target_db``."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp_path = Path(tmp.name)
    try:
        export_trade_log_json(source_db, tmp_path, sources=sources)
        return import_trade_log_json(target_db, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _count_trade_rows(db_path: Path) -> int | None:
    """Return manual/csv trade count, or None when the file is not a readable SQLite DB."""
    if not _sqlite_file_is_readable(db_path):
        return None
    try:
        conn = _connect_ro(db_path)
    except sqlite3.Error:
        return None
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_log_entries' LIMIT 1"
        ).fetchone():
            return 0
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM trade_log_entries WHERE source IN ('manual','csv')"
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _append_candidate(paths: list[Path], seen: set[str], path: Path) -> None:
    if not path.is_file():
        return
    key = str(path.resolve())
    if key in seen:
        return
    if not _sqlite_file_is_readable(path):
        return
    seen.add(key)
    paths.append(path)


def _resolve_db_path(slug: str | None, db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path.resolve()
    if not slug:
        raise SystemExit("Provide --slug or --db-path.")
    return resolve_league_sqlite_path(slug).resolve()


def _candidate_restore_paths(inst: Path, slug: str) -> list[Path]:
    names = [
        f"{slug}.db",
        _LEGACY_LEAGUE_DB_FILES.get(slug, ""),
        f"{slug}.db.test-bak",
        f"{_LEGACY_LEAGUE_DB_FILES.get(slug, '')}.test-bak",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for name in names:
        if not name:
            continue
        _append_candidate(out, seen, inst / name)
    for pattern in (f"{slug}.db.test-bak*", f"{_LEGACY_LEAGUE_DB_FILES.get(slug, '')}.test-bak*"):
        for p in sorted(inst.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name.endswith(("-shm", "-wal")):
                continue
            _append_candidate(out, seen, p)
    backup_dir = inst / "league_backups" / slug
    if backup_dir.is_dir():
        for p in sorted(backup_dir.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
            _append_candidate(out, seen, p)
    for p in sorted(inst.glob("*.corrupt-*.bak"), key=lambda x: x.stat().st_mtime, reverse=True):
        _append_candidate(out, seen, p)
    return out


def restore_from_candidates(slug: str, *, dry_run: bool = False) -> dict[str, object]:
    """Merge manual/csv trades from backup/legacy DB files into the active league DB."""
    target = resolve_league_sqlite_path(slug).resolve()
    inst = target.parent
    candidates = [p for p in _candidate_restore_paths(inst, slug) if p.resolve() != target.resolve()]
    report: dict[str, object] = {
        "target": str(target),
        "candidates": [str(p) for p in candidates],
        "merged": [],
        "totals": {"inserted": 0, "skipped_existing": 0, "skipped_unresolved": 0},
    }
    if dry_run:
        for src in candidates:
            n = _count_trade_rows(src)
            if n is None:
                report["merged"].append({"source": str(src), "rows": None, "error": "not a readable SQLite database"})
            else:
                report["merged"].append({"source": str(src), "rows": n})
        return report

    for src in candidates:
        if _count_trade_rows(src) is None:
            report["merged"].append(
                {"source": str(src), "inserted": 0, "skipped_existing": 0, "skipped_unresolved": 0, "error": "not a readable SQLite database"}
            )
            continue
        try:
            ins, skip_ex, skip_un = merge_trade_log_sqlite(target, src, sources=_PUBLIC_SOURCES)
        except (sqlite3.Error, OSError, ValueError) as exc:
            report["merged"].append(
                {
                    "source": str(src),
                    "inserted": 0,
                    "skipped_existing": 0,
                    "skipped_unresolved": 0,
                    "error": str(exc),
                }
            )
            continue
        if ins or skip_ex or skip_un:
            report["merged"].append(
                {
                    "source": str(src),
                    "inserted": ins,
                    "skipped_existing": skip_ex,
                    "skipped_unresolved": skip_un,
                }
            )
        totals = report["totals"]
        assert isinstance(totals, dict)
        totals["inserted"] = int(totals.get("inserted", 0)) + ins
        totals["skipped_existing"] = int(totals.get("skipped_existing", 0)) + skip_ex
        totals["skipped_unresolved"] = int(totals.get("skipped_unresolved", 0)) + skip_un
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="Export trade log rows to JSON")
    exp.add_argument("--slug")
    exp.add_argument("--db-path", type=Path)
    exp.add_argument("--out", type=Path, required=True)

    imp = sub.add_parser("import", help="Import trade log rows from JSON")
    imp.add_argument("--slug")
    imp.add_argument("--db-path", type=Path)
    imp.add_argument("--in", dest="in_path", type=Path, required=True)

    merge = sub.add_parser("merge-sqlite", help="Merge rows from another SQLite file")
    merge.add_argument("--slug")
    merge.add_argument("--db-path", type=Path)
    merge.add_argument("--from-db", type=Path, required=True)

    restore = sub.add_parser("restore-candidates", help="Scan legacy/backup DBs and merge into active DB")
    restore.add_argument("--slug", required=True)
    restore.add_argument("--dry-run", action="store_true")

    ns = p.parse_args()
    if ns.cmd == "export":
        db = _resolve_db_path(getattr(ns, "slug", None), getattr(ns, "db_path", None))
        n = export_trade_log_json(db, ns.out)
        print(f"exported {n} row(s) from {db} -> {ns.out}")
        return 0
    if ns.cmd == "import":
        db = _resolve_db_path(getattr(ns, "slug", None), getattr(ns, "db_path", None))
        ins, skip_ex, skip_un = import_trade_log_json(db, ns.in_path)
        print(f"imported {ins}, skipped existing {skip_ex}, skipped unresolved teams {skip_un} -> {db}")
        return 0
    if ns.cmd == "merge-sqlite":
        db = _resolve_db_path(getattr(ns, "slug", None), getattr(ns, "db_path", None))
        ins, skip_ex, skip_un = merge_trade_log_sqlite(db, ns.from_db)
        print(f"merged {ins}, skipped existing {skip_ex}, skipped unresolved teams {skip_un} -> {db}")
        return 0
    if ns.cmd == "restore-candidates":
        report = restore_from_candidates(ns.slug, dry_run=bool(ns.dry_run))
        print(json.dumps(report, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
