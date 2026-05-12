"""Process-local cache for layout nav teams (avoids loading all teams on every request)."""
from __future__ import annotations

from pathlib import Path

from flask import Flask
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

_NAV_TEAMS_CACHE: dict[int, tuple[str, list]] = {}


def _sqlite_league_db_fingerprint(engine: Engine) -> str | None:
    url = engine.url
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    db = url.database
    if db == ":memory:" or db.startswith("file::memory:"):
        return None
    path = Path(db)
    if not path.is_file():
        return None
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _nav_teams_db_fallback_fingerprint() -> str:
    """Row stats when the league DB is not a normal on-disk SQLite file (e.g. in-memory tests)."""
    from app.models import Team, db

    row = db.session.execute(select(func.count(Team.id), func.max(Team.id), func.min(Team.id))).one()
    return f"tbl:{row[0]}:{row[1]}:{row[2]}"


def get_nav_teams_for_layout(app: Flask) -> list:
    from app.models import Team, db

    engine = db.engine
    fp = _sqlite_league_db_fingerprint(engine)
    if fp is None:
        fp = f"{str(app.config.get('LEAGUE_SLUG') or '')}:{_nav_teams_db_fallback_fingerprint()}"

    aid = id(app)
    ent = _NAV_TEAMS_CACHE.get(aid)
    if ent is not None and ent[0] == fp:
        return ent[1]

    teams = list(db.session.scalars(select(Team)).all())
    teams.sort(key=lambda t: (t.full_display_name() or "").strip().lower())
    _NAV_TEAMS_CACHE[aid] = (fp, teams)
    return teams
