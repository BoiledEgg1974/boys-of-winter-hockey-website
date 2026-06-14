"""GM export attendance tracker: rolling calendar, registration, and gap warnings."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.models import Team
from app.services.gm_messaging import create_gm_message, gm_display_name
from app.site_models import GmExportAttendance, GmLeagueMembership, User

ATTENDANCE_WINDOW_DAYS = 45
GAP_WARNING_THRESHOLD_DAYS = 8

_EXPORT_GAP_WARNING_BODY = (
    "Hello {gm_name}. We hope you're doing well. I wanted to check in regarding your "
    "participation in {league_slug}. We've noticed that your exports have become scarce "
    "and we're concerned that you may be too busy or no longer interested in continuing. "
    "We truly value you and everything you've contributed to the league, and we "
    "completely understand that life can get hectic or priorities can change. At the "
    "same time, to keep things running smoothly and fairly for everyone, the league "
    "needs to ensure that all GM roles are being actively fulfilled. With that in mind, "
    "we need to know whether you'd like to continue with the league in your current "
    "capacity. If you feel you can't commit to the required export schedule going "
    "forward, we will unfortunately have no choice but to seek a replacement for the "
    "role. Could you please let us know your plans at your earliest convenience, "
    "ideally within the next couple of days? Your prompt reply will help us make the "
    "necessary arrangements, whatever you decide, and we'll be respectful and "
    "supportive either way. You can message the Commissioner. Thank you, and we look "
    "forward to hearing from you."
)


def parse_export_date(raw: str | None, *, default: date | None = None) -> date:
    """Parse YYYY-MM-DD from admin export form; fall back to UTC today."""
    text = str(raw or "").strip()
    if text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return default or datetime.utcnow().date()


def rolling_attendance_window_dates(
    *,
    anchor: date | None = None,
    days: int = ATTENDANCE_WINDOW_DAYS,
) -> list[date]:
    """Inclusive rolling window ending at anchor, newest date first."""
    end = anchor or datetime.utcnow().date()
    start = end - timedelta(days=max(0, int(days) - 1))
    out: list[date] = []
    cur = end
    while cur >= start:
        out.append(cur)
        cur -= timedelta(days=1)
    return out


def previous_export_date_for_team(
    session,
    league_slug: str,
    team_id: int,
    *,
    before_date: date,
) -> date | None:
    return session.scalar(
        select(func.max(GmExportAttendance.export_date)).where(
            GmExportAttendance.league_slug == league_slug,
            GmExportAttendance.team_id == int(team_id),
            GmExportAttendance.export_date < before_date,
        )
    )


def last_export_date_for_team(session, league_slug: str, team_id: int) -> date | None:
    return session.scalar(
        select(func.max(GmExportAttendance.export_date)).where(
            GmExportAttendance.league_slug == league_slug,
            GmExportAttendance.team_id == int(team_id),
        )
    )


def export_gap_warning_body(*, gm_name: str, league_slug: str) -> str:
    return _EXPORT_GAP_WARNING_BODY.format(
        gm_name=str(gm_name or "GM").strip() or "GM",
        league_slug=str(league_slug or "the league").strip() or "the league",
    )


def register_export_attendance(
    session,
    *,
    league_slug: str,
    team_id: int,
    export_date: date,
    checked_by_user_id: int | None,
    ap_ledger_entry_id: int | None = None,
    flush: bool = True,
) -> tuple[GmExportAttendance, bool]:
    """
    Insert attendance for team/date if missing (idempotent).
    Returns (row, created_new).
    """
    existing = session.scalar(
        select(GmExportAttendance).where(
            GmExportAttendance.league_slug == league_slug,
            GmExportAttendance.team_id == int(team_id),
            GmExportAttendance.export_date == export_date,
        ).limit(1)
    )
    if existing is not None:
        if ap_ledger_entry_id is not None and existing.ap_ledger_entry_id is None:
            existing.ap_ledger_entry_id = int(ap_ledger_entry_id)
        return existing, False

    prev = previous_export_date_for_team(
        session,
        league_slug,
        int(team_id),
        before_date=export_date,
    )
    gap_days = (export_date - prev).days if prev is not None else None
    row = GmExportAttendance(
        league_slug=league_slug,
        team_id=int(team_id),
        export_date=export_date,
        checked_by_user_id=checked_by_user_id,
        created_at=datetime.utcnow(),
        ap_ledger_entry_id=ap_ledger_entry_id,
        previous_export_date=prev,
        gap_days=gap_days,
    )
    session.add(row)
    if flush:
        session.flush()
    return row, True


def maybe_send_export_gap_warning(
    session,
    *,
    attendance_row: GmExportAttendance,
    league_slug: str,
    admin_user_id: int,
) -> bool:
    """Send in-site + Discord DM warning once when gap exceeds threshold."""
    if attendance_row.gap_warning_sent_at is not None:
        return False
    gap = attendance_row.gap_days
    if gap is None or int(gap) <= GAP_WARNING_THRESHOLD_DAYS:
        return False

    mem = session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(attendance_row.team_id),
            GmLeagueMembership.status == "active",
        )
        .limit(1)
    )
    if mem is None:
        return False

    gm_user = session.get(User, int(mem.user_id))
    if gm_user is None:
        return False

    body = export_gap_warning_body(
        gm_name=gm_display_name(gm_user),
        league_slug=league_slug,
    )
    create_gm_message(
        league_slug=league_slug,
        from_user_id=int(admin_user_id),
        to_user_id=int(mem.user_id),
        body=body,
        event_key="export_gap_warning",
    )
    attendance_row.gap_warning_sent_at = datetime.utcnow()
    return True


def _current_gap_days(
    *,
    last_export: date | None,
    anchor: date,
) -> int | None:
    if last_export is None:
        return None
    return max(0, (anchor - last_export).days)


def build_attendance_tracker_payload(
    session,
    league_slug: str,
    *,
    anchor: date | None = None,
    logo_resolver=None,
) -> dict[str, Any]:
    """Build rolling 45-day tracker grid for active GM teams."""
    end = anchor or datetime.utcnow().date()
    window_dates = rolling_attendance_window_dates(anchor=end)
    window_set = set(window_dates)
    oldest = window_dates[-1] if window_dates else end

    memberships = list(
        session.scalars(
            select(GmLeagueMembership)
            .where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.status == "active",
            )
            .order_by(GmLeagueMembership.team_id)
        ).all()
    )
    team_ids = sorted({int(m.team_id) for m in memberships})
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        teams_by_id = {
            int(t.id): t
            for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        }

    attendance_rows = list(
        session.scalars(
            select(GmExportAttendance).where(
                GmExportAttendance.league_slug == league_slug,
                GmExportAttendance.team_id.in_(team_ids) if team_ids else False,
                GmExportAttendance.export_date >= oldest,
                GmExportAttendance.export_date <= end,
            )
        ).all()
    ) if team_ids else []

    exports_by_team: dict[int, set[date]] = {tid: set() for tid in team_ids}
    for row in attendance_rows:
        if row.export_date in window_set:
            exports_by_team.setdefault(int(row.team_id), set()).add(row.export_date)

    last_export_by_team: dict[int, date | None] = {}
    for tid in team_ids:
        last_export_by_team[tid] = last_export_date_for_team(session, league_slug, tid)

    tracker_rows: list[dict[str, Any]] = []
    for mem in memberships:
        tid = int(mem.team_id)
        team = teams_by_id.get(tid)
        gm_user = session.get(User, int(mem.user_id))
        exported_dates = exports_by_team.get(tid, set())
        cells = [
            {
                "date": d.isoformat(),
                "exported": d in exported_dates,
                "label": d.strftime("%b %d"),
            }
            for d in window_dates
        ]
        last_export = last_export_by_team.get(tid)
        gap_days = _current_gap_days(last_export=last_export, anchor=end)
        tracker_rows.append(
            {
                "team_id": tid,
                "team_name": team.full_display_name() if team else f"Team {tid}",
                "gm_name": gm_display_name(gm_user),
                "logo_url": logo_resolver(team) if logo_resolver and team else "",
                "total_exports": len(exported_dates),
                "current_gap_days": gap_days,
                "gap_warning": gap_days is not None and gap_days > GAP_WARNING_THRESHOLD_DAYS,
                "last_export_date": last_export.isoformat() if last_export else None,
                "cells": cells,
            }
        )

    tracker_rows.sort(key=lambda r: (r["team_name"] or "").lower())

    return {
        "window_days": ATTENDANCE_WINDOW_DAYS,
        "gap_threshold_days": GAP_WARNING_THRESHOLD_DAYS,
        "anchor_date": end.isoformat(),
        "dates": [d.isoformat() for d in window_dates],
        "date_labels": [d.strftime("%b %d") for d in window_dates],
        "rows": tracker_rows,
    }
