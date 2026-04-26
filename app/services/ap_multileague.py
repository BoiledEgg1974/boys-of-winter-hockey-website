"""Look up team ids across league SQLite files (franchises keyed by ``slug``)."""
from __future__ import annotations

import sqlite3

from app.config import resolve_league_sqlite_path


def team_id_for_slug_in_league(league_slug: str, team_slug: str) -> int | None:
    """Return ``teams.id`` for ``team_slug`` in that league's DB, or None if missing."""
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file():
        return None
    ts = (team_slug or "").strip()
    if not ts:
        return None
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM teams WHERE lower(slug) = lower(?) LIMIT 1",
            (ts,),
        ).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()
