"""In-game timeline helpers for org development reports (hockey season months)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, Season
from app.services.seasons import get_current_season, season_display_label

# July = 0 … June = 11 within a hockey season.
_HOCKEY_MONTH_ORDER: dict[int, int] = {7: 0, 8: 1, 9: 2, 10: 3, 11: 4, 12: 5, 1: 6, 2: 7, 3: 8, 4: 9, 5: 10, 6: 11}

_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

ORG_DEV_ARCHIVE_MONTH_LIMIT = 36


def hockey_season_start_year(d: date) -> int:
    """League year that contains ``d`` (July 1 boundary)."""
    return int(d.year if d.month >= 7 else d.year - 1)


def timeline_from_date(d: date) -> dict[str, int | str]:
    season_start = hockey_season_start_year(d)
    month = int(d.month)
    year = int(d.year)
    return {
        "timeline_season_start_year": season_start,
        "timeline_calendar_year": year,
        "timeline_calendar_month": month,
        "timeline_key": f"{year:04d}-{month:02d}",
        "sort_key": timeline_sort_key(season_start, year, month),
    }


def timeline_sort_key(season_start_year: int, calendar_year: int, calendar_month: int) -> tuple[int, int]:
    return (int(season_start_year), _HOCKEY_MONTH_ORDER.get(int(calendar_month), 99))


def timeline_label(
    *,
    calendar_year: int,
    calendar_month: int,
    season_start_year: int | None = None,
) -> str:
    month_name = _MONTH_NAMES[int(calendar_month)] if 1 <= int(calendar_month) <= 12 else "Unknown"
    sy = int(season_start_year if season_start_year is not None else hockey_season_start_year(
        date(int(calendar_year), int(calendar_month), 1)
    ))
    season_lbl = season_display_label(Season(start_year=sy, label="", is_current=False))
    return f"{month_name} {int(calendar_year)} ({season_lbl})"


def development_report_title(
    *,
    calendar_year: int,
    calendar_month: int,
    season_start_year: int | None = None,
) -> str:
    return f"{timeline_label(calendar_year=calendar_year, calendar_month=calendar_month, season_start_year=season_start_year)} Development Report"


def league_timeline_anchor_date(session: Session, season: Season | None = None) -> date:
    """Latest in-world game date for the active season; real-world today as fallback."""
    season = season or get_current_season()
    if season is None:
        return date.today()
    anchor = session.scalar(
        select(func.max(Game.game_date)).where(
            Game.season_id == season.id,
            Game.status == "final",
            Game.game_date.isnot(None),
        )
    )
    if anchor:
        return anchor
    anchor2 = session.scalar(
        select(func.max(Game.game_date)).where(
            Game.season_id == season.id,
            Game.game_date.isnot(None),
        )
    )
    if anchor2:
        return anchor2
    return date.today()


def timeline_from_snapshot(snap: object, *, fallback_anchor: date | None = None) -> dict[str, int | str]:
    """Resolve in-game month for a rating snapshot (stored columns or anchor fallback)."""
    sy = getattr(snap, "timeline_season_start_year", None)
    cy = getattr(snap, "timeline_calendar_year", None)
    cm = getattr(snap, "timeline_calendar_month", None)
    if sy is not None and cy is not None and cm is not None:
        return {
            "timeline_season_start_year": int(sy),
            "timeline_calendar_year": int(cy),
            "timeline_calendar_month": int(cm),
            "timeline_key": f"{int(cy):04d}-{int(cm):02d}",
            "sort_key": timeline_sort_key(int(sy), int(cy), int(cm)),
        }
    at = getattr(snap, "snapshot_at", None)
    if isinstance(at, datetime):
        return timeline_from_date(at.date())
    if fallback_anchor is not None:
        return timeline_from_date(fallback_anchor)
    return timeline_from_date(date.today())
