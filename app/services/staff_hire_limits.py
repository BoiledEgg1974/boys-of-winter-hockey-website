"""Daily hire request limits on the league calendar (Jul 1–Oct 1 vs Oct 2–Jun 30)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.site_models import StaffChangeRequest

# Jul 1 – Oct 1 (inclusive): 3 hires per team per calendar day; otherwise 1.
_HIGH_SEASON_LIMIT = 3
_LOW_SEASON_LIMIT = 1


def hire_limit_for_calendar_date(d: date) -> int:
    if d.month in (7, 8, 9):
        return _HIGH_SEASON_LIMIT
    if d.month == 10 and d.day == 1:
        return _HIGH_SEASON_LIMIT
    return _LOW_SEASON_LIMIT


def hire_window_label(d: date) -> str:
    if hire_limit_for_calendar_date(d) == _HIGH_SEASON_LIMIT:
        return "Jul 1 – Oct 1 (up to 3 hires per day)"
    return "Oct 2 – Jun 30 (up to 1 hire per day)"


@dataclass(frozen=True)
class HireLimitStatus:
    limit: int
    used: int
    remaining: int
    window_label: str
    date_label: str

    @property
    def limit_reached(self) -> bool:
        return self.remaining <= 0


def count_hire_requests_today(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    on_date: date | None = None,
) -> int:
    d = on_date or date.today()
    start = datetime(d.year, d.month, d.day)
    end = datetime(d.year, d.month, d.day, 23, 59, 59, 999999)
    n = session.scalar(
        select(func.count())
        .select_from(StaffChangeRequest)
        .where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.team_id == int(team_id),
            StaffChangeRequest.request_type == "hire",
            StaffChangeRequest.status.in_(("pending", "approved")),
            StaffChangeRequest.created_at >= start,
            StaffChangeRequest.created_at <= end,
        )
    )
    return int(n or 0)


def hire_limit_status(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    on_date: date | None = None,
) -> HireLimitStatus:
    d = on_date or date.today()
    limit = hire_limit_for_calendar_date(d)
    used = count_hire_requests_today(session, league_slug=league_slug, team_id=team_id, on_date=d)
    remaining = max(0, limit - used)
    return HireLimitStatus(
        limit=limit,
        used=used,
        remaining=remaining,
        window_label=hire_window_label(d),
        date_label=d.strftime("%Y-%m-%d"),
    )
