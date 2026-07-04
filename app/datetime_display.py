"""Display helpers for league timestamps stored as UTC-naive datetimes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

LEAGUE_DISPLAY_TZ = ZoneInfo("America/New_York")
LEAGUE_DISPLAY_TZ_LABEL = "ET"


def eastern_naive_from_utc_naive(utc_naive: datetime) -> datetime:
    """Convert a UTC-naive datetime to Eastern wall time (naive)."""
    aware_utc = utc_naive.replace(tzinfo=timezone.utc)
    return aware_utc.astimezone(LEAGUE_DISPLAY_TZ).replace(tzinfo=None)


def coerce_utc_naive_datetime(value: datetime | str | None) -> datetime | None:
    """Normalize a UTC timestamp from DB rows or ISO strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = f"{s[:-1]}+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def format_utc_naive_eastern(
    dt: datetime | str | None,
    *,
    fmt: str = "%Y-%m-%d %H:%M",
    with_label: bool = True,
) -> str:
    """Format a UTC timestamp for league admin/GM UI in Eastern Time."""
    parsed = coerce_utc_naive_datetime(dt)
    if parsed is None:
        return ""
    text = eastern_naive_from_utc_naive(parsed).strftime(fmt)
    if with_label:
        text = f"{text} {LEAGUE_DISPLAY_TZ_LABEL}"
    return text


def register_eastern_time_template_filter(app: Any) -> None:
    """Register ``eastern_time`` Jinja filter on a Flask app."""

    @app.template_filter("eastern_time")
    def eastern_time_filter(dt: object, fmt: str = "%Y-%m-%d %H:%M") -> str:
        if dt is None or dt == "":
            return ""
        if isinstance(dt, datetime):
            return format_utc_naive_eastern(dt, fmt=fmt)
        if isinstance(dt, str):
            return format_utc_naive_eastern(dt, fmt=fmt)
        return ""
