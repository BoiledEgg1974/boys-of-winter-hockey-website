from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, PlayerGoalieStat, PlayerSkaterStat, Season, TeamStanding, db

_PROCESS_CURRENT_SEASON: dict[str, tuple[str, int | None]] = {}


def season_display_label(season: Season | None) -> str:
    """Short label for UI (e.g. ``1968-69``).

    Boys of Winter league year runs **July 1** through **June 30**; the canonical
    label is the usual hockey form ``{start_year}-{(start_year+1) % 100:02d}`` when
    ``start_year`` is set on the season row. Otherwise falls back to ``Season.label``.
    """
    if season is None:
        return ""
    if season.start_year is not None:
        y = int(season.start_year)
        end = y + 1
        return f"{y}-{end % 100:02d}"
    return (season.label or "").strip() or "—"


def season_age_reference_date(season: Season | None) -> date:
    """Calendar date used for 'as of' player age while a league season is active.

    League season boundary is **July 1** through **June 30** of the following calendar
    year, so the reference point is July 1 of ``start_year``. If only ``end_year`` is
    present, uses July 1 of ``end_year - 1``. Falls back to today when the season
    record has no years or when no season exists.
    """
    if season is None:
        return date.today()
    if season.start_year is not None:
        return date(season.start_year, 7, 1)
    if season.end_year is not None:
        return date(max(season.end_year - 1, 1), 7, 1)
    return date.today()


def _season_id_with_latest_game_date() -> int | None:
    """Season that contains the globally latest ``Game.game_date`` (when dates exist)."""
    gsid = db.session.scalar(
        select(Game.season_id)
        .where(Game.game_date.isnot(None))
        .group_by(Game.season_id)
        .order_by(func.max(Game.game_date).desc(), Game.season_id.desc())
        .limit(1)
    )
    return int(gsid) if gsid is not None else None


def _season_highest_start_year() -> Season | None:
    """Newest league year by ``Season.start_year`` (then ``id``).

    Used before the latest-game fallback so a newly added season (e.g. 1968–69) wins over
    the previous year that still owns the most recent playoff ``game_date`` (e.g. June 1968
    finals on the 1967–68 row) when no ``is_current`` flag is set yet.
    """
    return db.session.scalars(
        select(Season)
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        .limit(1)
    ).first()


def _season_state_fingerprint() -> str:
    """Invalidate season cache when the league SQLite file changes (or team stats in :memory: tests)."""
    from flask import current_app, has_request_context

    from app.services.layout_nav_cache import (
        _nav_teams_db_fallback_fingerprint,
        league_engine_sqlite_fingerprint,
    )

    fp = league_engine_sqlite_fingerprint(db.engine)
    if fp is not None:
        return fp
    slug = ""
    if has_request_context():
        try:
            slug = str(current_app.config.get("LEAGUE_SLUG") or "")
        except RuntimeError:
            pass
    return f"{slug}:{_nav_teams_db_fallback_fingerprint()}"


def _resolve_current_season() -> Season | None:
    fhm_current = db.session.scalars(
        select(Season)
        .where(
            Season.is_current.is_(True),
            Season.fhm_season_id.isnot(None),
            Season.fhm_season_id.like("fhm-league%"),
        )
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        .limit(1)
    ).first()
    if fhm_current:
        return fhm_current

    flagged = db.session.scalars(
        select(Season)
        .where(Season.is_current.is_(True))
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
    ).first()

    if flagged:
        return flagged

    by_year = _season_highest_start_year()
    if by_year is not None:
        return by_year

    sid_latest = _season_id_with_latest_game_date()

    if sid_latest is not None:
        s = db.session.get(Season, int(sid_latest))
        if s is not None:
            return s

    sid = db.session.scalar(
        select(TeamStanding.season_id, func.count(TeamStanding.id).label("n"))
        .group_by(TeamStanding.season_id)
        .order_by(func.count(TeamStanding.id).desc(), TeamStanding.season_id.desc())
        .limit(1)
    )
    if sid is not None:
        s = db.session.get(Season, int(sid))
        if s is not None:
            return s

    return db.session.scalar(select(Season).order_by(Season.id.desc()).limit(1))


def get_current_season() -> Season | None:
    """Return the active season for standings, stats, schedule, etc.

    Order of resolution:
    1. FHM mount row: ``is_current`` and ``fhm_season_id`` like ``fhm-league%`` (must win
       over any other ``is_current`` row so statistics use the same ``Season`` FHM imports
       write player aggregates to).
    2. Else any season with ``is_current`` true (highest ``start_year``, then id).
    3. Else the season with the highest ``start_year`` (then id) — avoids sticking on the
       prior year when its playoffs hold the latest ``game_date`` but a newer season row
       already exists.
    4. Else the season that owns the latest dated ``Game``.
    5. Else the season that owns the largest ``TeamStanding`` import.
    6. Else the season row with the highest ``id``.

    Resolved season is cached per worker (keyed by DB URL + file mtime/size) so repeated
    calls avoid the full query chain; each hit still does a single ``Session.get`` by id.
    """
    from flask import g, has_request_context

    lane = str(db.engine.url)
    fp0 = _season_state_fingerprint()

    if has_request_context():
        hit = getattr(g, "_get_current_season_cached", None)
        if isinstance(hit, tuple) and len(hit) == 3 and hit[0] == fp0 and hit[1] == lane:
            return hit[2]

    ent = _PROCESS_CURRENT_SEASON.get(lane)
    if ent is not None and ent[0] == fp0:
        sid = ent[1]
        if sid is None:
            season: Season | None = None
        else:
            season = db.session.get(Season, sid)
            if season is None:
                season = _resolve_current_season()
                _PROCESS_CURRENT_SEASON[lane] = (fp0, season.id if season else None)
        if has_request_context():
            g._get_current_season_cached = (fp0, lane, season)
        return season

    season = _resolve_current_season()
    _PROCESS_CURRENT_SEASON[lane] = (fp0, season.id if season else None)
    if has_request_context():
        g._get_current_season_cached = (fp0, lane, season)
    return season


def _season_has_imported_dashboard_data(session: Session, season_id: int) -> bool:
    """True if this season has any standings, games, or player stat rows (homepage / tables)."""
    if session.scalar(select(func.count()).select_from(TeamStanding).where(TeamStanding.season_id == season_id)):
        return True
    if session.scalar(select(func.count()).select_from(Game).where(Game.season_id == season_id)):
        return True
    if session.scalar(
        select(func.count()).select_from(PlayerSkaterStat).where(PlayerSkaterStat.season_id == season_id)
    ):
        return True
    if session.scalar(
        select(func.count()).select_from(PlayerGoalieStat).where(PlayerGoalieStat.season_id == season_id)
    ):
        return True
    return False


def season_with_imported_data_fallback(session: Session, current: Season | None) -> Season | None:
    """Use ``current`` unless it has no imported stats/games yet.

    When the league year advances (new ``Season`` row + ``is_current``) before CSV/FHM
    data is re-pointed, ``get_current_season()`` still returns that row but the homepage
    and standings would be empty. In that case, return the newest **older** season that
    already has standings, games, or player stats so the site stays populated until the
    new year is imported.

    Playoff bracket and ``is_current`` semantics should keep using ``get_current_season()``
    alone so an empty new year still shows an empty bracket.
    """
    if current is None:
        return None
    if _season_has_imported_dashboard_data(session, int(current.id)):
        return current
    if current.start_year is not None:
        older = session.scalars(
            select(Season)
            .where(
                Season.id != current.id,
                Season.start_year.isnot(None),
                Season.start_year < int(current.start_year),
            )
            .order_by(Season.start_year.desc(), Season.id.desc())
        ).all()
    else:
        older = session.scalars(
            select(Season).where(Season.id != current.id).order_by(Season.id.desc())
        ).all()
    for s in older:
        if _season_has_imported_dashboard_data(session, int(s.id)):
            return s
    return current
