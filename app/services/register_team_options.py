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
        cur = conn.execute("PRAGMA table_info(teams)")
        cols = {row[1] for row in cur.fetchall()}
        if "fhm_team_id" in cols:
            cur = conn.execute(
                "SELECT id, name, abbreviation, fhm_team_id FROM teams ORDER BY name COLLATE NOCASE"
            )
            rows = cur.fetchall()
            out: list[dict[str, int | str]] = []
            for r in rows:
                fid = r[3]
                out.append(
                    {
                        "id": int(r[0]),
                        "name": r[1] or "",
                        "abbr": r[2] or "",
                        "fhm_team_id": (str(fid).strip() if fid is not None and str(fid).strip() else ""),
                    }
                )
            return out
        cur = conn.execute(
            "SELECT id, name, abbreviation FROM teams ORDER BY name COLLATE NOCASE"
        )
        return [{"id": int(r[0]), "name": r[1] or "", "abbr": r[2] or "", "fhm_team_id": ""} for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def fhm_team_id_for_league_team(league_slug: str, team_pk: int) -> str | None:
    """Return ``teams.fhm_team_id`` for a league DB row ``teams.id == team_pk``."""
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file() or team_pk <= 0:
        return None
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        cur = conn.execute("PRAGMA table_info(teams)")
        if "fhm_team_id" not in {row[1] for row in cur.fetchall()}:
            return None
        cur = conn.execute(
            "SELECT fhm_team_id FROM teams WHERE id = ? LIMIT 1", (int(team_pk),)
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        s = str(row[0]).strip()
        return s or None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def team_snapshot_for_membership(league_slug: str, team_pk: int) -> dict[str, str]:
    """Human-readable team row for admin UI (best-effort from league SQLite)."""
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file() or team_pk <= 0:
        return {"name": "", "abbr": "", "fhm_team_id": ""}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return {"name": "", "abbr": "", "fhm_team_id": ""}
    try:
        cur = conn.execute("PRAGMA table_info(teams)")
        cols = {row[1] for row in cur.fetchall()}
        if "fhm_team_id" in cols:
            cur = conn.execute(
                "SELECT name, abbreviation, fhm_team_id FROM teams WHERE id = ? LIMIT 1",
                (int(team_pk),),
            )
        else:
            cur = conn.execute(
                "SELECT name, abbreviation FROM teams WHERE id = ? LIMIT 1",
                (int(team_pk),),
            )
        row = cur.fetchone()
        if not row:
            return {"name": "", "abbr": "", "fhm_team_id": ""}
        name, abbr = row[0] or "", row[1] or ""
        fhm = ""
        if len(row) > 2 and row[2] is not None:
            fhm = str(row[2]).strip()
        return {"name": name, "abbr": abbr, "fhm_team_id": fhm}
    except sqlite3.Error:
        return {"name": "", "abbr": "", "fhm_team_id": ""}
    finally:
        conn.close()


def all_league_team_options() -> dict[str, list[dict[str, int | str]]]:
    return {slug: teams_for_registration(slug) for slug in league_slugs()}
