from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models import Game, Season, TeamStanding, db


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


def get_current_season() -> Season | None:
    """Return the active season for standings, stats, schedule, etc.

    Order of resolution:
    1. Any season with ``is_current`` true (deterministic: highest ``start_year``, then id),
       unless schedule data clearly belongs to a **later** season row (see below).
    2. If no ``is_current``, the season that owns the latest dated ``Game``.
    3. Else the season that owns the largest ``TeamStanding`` import.
    4. Else the season row with the highest ``id``.

    When ``is_current`` still points at an older season but imported games exist only
    on a newer ``Season`` row (higher ``start_year``), or the flagged season has no
    games while another season has the latest ``game_date``, the schedule wins so
    statistics stay aligned with July–June sim progression.
    """
    flagged = db.session.scalars(
        select(Season)
        .where(Season.is_current.is_(True))
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
    ).first()

    sid_latest = _season_id_with_latest_game_date()

    if flagged and sid_latest is not None and int(sid_latest) != int(flagged.id):
        s_latest = db.session.get(Season, int(sid_latest))
        if s_latest is not None:
            max_d_flagged = db.session.scalar(
                select(func.max(Game.game_date)).where(
                    Game.season_id == flagged.id,
                    Game.game_date.isnot(None),
                )
            )
            fy = flagged.start_year
            gy = s_latest.start_year
            if max_d_flagged is None:
                return s_latest
            if fy is not None and gy is not None and gy > fy:
                return s_latest

    if flagged:
        return flagged

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
