"""Look up team ids across league SQLite files (franchises keyed by ``slug``)."""
from __future__ import annotations

import sqlite3

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import resolve_league_sqlite_path


def team_id_for_slug_in_league(
    league_slug: str,
    team_slug: str,
    *,
    orm_session: Session | None = None,
    orm_league_slug: str | None = None,
) -> int | None:
    """Return ``teams.id`` for ``team_slug`` in that league's DB, or None if missing.

    For the **current** Flask mount (``league_slug`` matches the running app), pass
    ``orm_session`` and ``orm_league_slug`` so the id is read from the same
    SQLAlchemy engine the app uses. Otherwise a fresh
    :func:`app.config.resolve_league_sqlite_path` at request time can point at a
    different on-disk file than the one bound at process start (e.g. new
    ``instance/<slug>.db`` vs legacy ``bow.db``), and ledger ``team_id`` would
    not match :func:`app.services.ap_service.team_ap_balance` for the admin list.
    """
    ts = (team_slug or "").strip()
    if not ts:
        return None
    if (
        orm_session is not None
        and (orm_league_slug or "").strip()
        and league_slug == orm_league_slug
    ):
        from app.models import Team

        row = orm_session.scalar(
            select(Team.id).where(func.lower(Team.slug) == func.lower(ts)).limit(1)
        )
        return int(row) if row is not None else None
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file():
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
