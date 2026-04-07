from __future__ import annotations

from datetime import date

from app.models import Season, db


def season_age_reference_date(season: Season | None) -> date:
    """Calendar date used for 'as of' player age while a league season is active.

    Uses October 1 of the season's start year (typical hockey year start). If only
    ``end_year`` is present, uses October 1 of ``end_year - 1``. Falls back to
    today when the season record has no years or when no season exists.
    """
    if season is None:
        return date.today()
    if season.start_year is not None:
        return date(season.start_year, 10, 1)
    if season.end_year is not None:
        return date(max(season.end_year - 1, 1), 10, 1)
    return date.today()


def get_current_season() -> Season | None:
    s = db.session.scalar(db.select(Season).filter_by(is_current=True).limit(1))
    if s:
        return s
    return db.session.scalar(db.select(Season).order_by(Season.id.desc()).limit(1))
