"""In-game timeline helpers for org development reports (hockey season months)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlayerRatingSnapshot, Season
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


def shift_calendar_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Shift a calendar year/month by ``delta`` months (negative = earlier)."""
    idx = int(year) * 12 + (int(month) - 1) + int(delta)
    return idx // 12, (idx % 12) + 1


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
    from app.services.homepage_dashboard import league_calendar_anchor_date

    season = season or get_current_season()
    if season is None:
        return date.today()
    return league_calendar_anchor_date(session, int(season.id))


def timeline_from_snapshot(snap: object, *, fallback_anchor: date | None = None) -> dict[str, int | str]:
    """Resolve in-game month for a rating snapshot.

    Prefer stored timeline columns, then the league sim-calendar anchor.
    Never use ``snapshot_at`` (wall-clock import time) for month labels.
    """
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
    if fallback_anchor is not None:
        return timeline_from_date(fallback_anchor)
    return timeline_from_date(date.today())


def map_import_months_onto_game_timeline(
    import_year_months: list[tuple[int, int]],
    *,
    anchor: date,
) -> dict[tuple[int, int], dict[str, int | str]]:
    """Map chronologically ordered real-world import months onto consecutive sim months.

    Newest import month lands on the league anchor month; earlier imports step
    backward one calendar month each so monthly diffs stay distinct.
    """
    unique = sorted({(int(y), int(m)) for y, m in import_year_months})
    if not unique:
        return {}
    anchor_tl = timeline_from_date(anchor)
    out: dict[tuple[int, int], dict[str, int | str]] = {}
    for offset, (iy, im) in enumerate(reversed(unique)):
        gy, gm = shift_calendar_month(
            int(anchor_tl["timeline_calendar_year"]),
            int(anchor_tl["timeline_calendar_month"]),
            -offset,
        )
        out[(iy, im)] = timeline_from_date(date(gy, gm, 1))
    return out


def backfill_null_snapshot_timelines(session: Session, *, anchor: date | None = None) -> int:
    """Stamp NULL ``timeline_*`` columns from real import months mapped onto the sim calendar.

    Returns the number of snapshot rows updated.
    """
    season = get_current_season()
    anchor_d = anchor or league_timeline_anchor_date(session, season)
    null_snaps = list(
        session.scalars(
            select(PlayerRatingSnapshot).where(
                PlayerRatingSnapshot.timeline_calendar_year.is_(None)
            )
        ).all()
    )
    if not null_snaps:
        return 0

    import_months: list[tuple[int, int]] = []
    for snap in null_snaps:
        at = getattr(snap, "snapshot_at", None)
        if isinstance(at, datetime):
            import_months.append((int(at.year), int(at.month)))
        else:
            import_months.append((int(anchor_d.year), int(anchor_d.month)))

    mapping = map_import_months_onto_game_timeline(import_months, anchor=anchor_d)
    updated = 0
    for snap in null_snaps:
        at = getattr(snap, "snapshot_at", None)
        if isinstance(at, datetime):
            key = (int(at.year), int(at.month))
        else:
            key = (int(anchor_d.year), int(anchor_d.month))
        tl = mapping.get(key) or timeline_from_date(anchor_d)
        snap.timeline_season_start_year = int(tl["timeline_season_start_year"])
        snap.timeline_calendar_year = int(tl["timeline_calendar_year"])
        snap.timeline_calendar_month = int(tl["timeline_calendar_month"])
        updated += 1
    if updated:
        session.flush()
    return updated
