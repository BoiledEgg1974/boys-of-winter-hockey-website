"""Read-only team lists for GM registration (hub has no league ORM)."""
from __future__ import annotations

import sqlite3

from app.config import league_slugs, resolve_league_sqlite_path


def teams_for_registration(league_slug: str) -> list[dict[str, int | str]]:
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = conn.execute(
            "SELECT id, name, abbreviation FROM teams ORDER BY name COLLATE NOCASE"
        )
        return [{"id": int(r[0]), "name": r[1] or "", "abbr": r[2] or ""} for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def all_league_team_options() -> dict[str, list[dict[str, int | str]]]:
    return {slug: teams_for_registration(slug) for slug in league_slugs()}
