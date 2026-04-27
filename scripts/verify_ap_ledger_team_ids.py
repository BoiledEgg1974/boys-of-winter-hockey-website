"""Report ``ap_ledger_entries`` rows whose ``team_id`` is missing from that league's teams table.

Uses the same ``resolve_league_sqlite_path`` as multileague lookups. Run from repo root::

    PYTHONPATH=. python scripts/verify_ap_ledger_team_ids.py

Optional: also flag rows where the id exists only in the alternate primary/legacy file
(when both exist), which indicates the old slug→id mismatch bug.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Repo root on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LEAGUE_SLUG", "bowl-fantasy")

from app.config import (  # noqa: E402
    BASE_DIR,
    _LEGACY_LEAGUE_DB_FILES,
    league_slugs,
    resolve_league_sqlite_path,
    resolve_site_sqlite_path,
)


def _team_exists(conn: sqlite3.Connection, team_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM teams WHERE id = ? LIMIT 1", (team_id,)).fetchone()
    return row is not None


def _alternate_league_path(slug: str) -> Path | None:
    """If both primary and legacy DB files exist, return the file *not* chosen by resolve."""
    inst = BASE_DIR / "instance"
    primary = inst / f"{slug}.db"
    legacy_name = _LEGACY_LEAGUE_DB_FILES.get(slug)
    legacy = inst / legacy_name if legacy_name else None
    if not legacy or not primary.is_file() or not legacy.is_file():
        return None
    chosen = resolve_league_sqlite_path(slug).resolve()
    p, l = primary.resolve(), legacy.resolve()
    if chosen == p:
        return legacy
    if chosen == l:
        return primary
    return None


def main() -> int:
    site = resolve_site_sqlite_path()
    if not site.is_file():
        print(f"No site DB at {site}")
        return 1

    site_conn = sqlite3.connect(str(site))
    try:
        site_conn.execute("SELECT 1 FROM ap_ledger_entries LIMIT 1")
    except sqlite3.Error as e:
        print(f"ap_ledger_entries: {e}")
        return 1

    rows = site_conn.execute(
        "SELECT id, league_slug, team_id, delta, reason_code, meta_json FROM ap_ledger_entries "
        "ORDER BY id"
    ).fetchall()
    print(f"Site: {site}")
    print(f"Ledger rows: {len(rows)}\n")

    bad: list[tuple] = []
    only_in_alt: list[tuple] = []

    for entry_id, league_slug, team_id, delta, reason_code, meta in rows:
        path = resolve_league_sqlite_path(league_slug)
        if not path.is_file():
            bad.append((entry_id, league_slug, team_id, f"no league db: {path}"))
            continue
        conn = sqlite3.connect(str(path))
        try:
            ok = _team_exists(conn, int(team_id))
        finally:
            conn.close()
        if not ok:
            bad.append((entry_id, league_slug, team_id, f"missing in {path.name}"))

        alt = _alternate_league_path(league_slug)
        if alt and alt.is_file() and not ok:
            c2 = sqlite3.connect(str(alt))
            try:
                if _team_exists(c2, int(team_id)):
                    only_in_alt.append(
                        (entry_id, league_slug, team_id, path.name, alt.name, delta, reason_code)
                    )
            finally:
                c2.close()

    if bad:
        print("ROWS WITH INVALID team_id FOR RESOLVED LEAGUE DB")
        print("(id, league_slug, team_id, detail)\n")
        for t in bad:
            print(f"  {t}")
    else:
        print("All ledger team_id values exist in resolve_league_sqlite_path DBs.")

    if only_in_alt:
        print()
        print(
            "STALE-ID SUSPECTS: id missing in chosen file but present in alternate instance file"
        )
        print("(id, league_slug, team_id, chosen_file, alt_file, delta, reason)\n")
        for t in only_in_alt:
            print(f"  {t}")

    # Per-league: max team id in DB vs ledger (quick sanity)
    print()
    print("League team id ranges (resolved path):")
    for slug in league_slugs():
        p = resolve_league_sqlite_path(slug)
        if not p.is_file():
            print(f"  {slug}: (no file)")
            continue
        c = sqlite3.connect(str(p))
        try:
            r = c.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM teams").fetchone()
        finally:
            c.close()
        print(f"  {slug}: teams id {r[0]}..{r[1]} count={r[2]} ({p.name})")

    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
