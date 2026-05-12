"""League History: First / Second all-star team tables from ``history_all_stars`` rows."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import HistoryAllStar, Season

_SEASON_TOKEN = re.compile(r"\b(\d{4}-\d{2})\b")
_LABEL_START_YEAR = re.compile(r"^(\d{4})-\d{2}$")


def all_star_logo_start_year_for_row(row: HistoryAllStar) -> int | None:
    """Calendar start year for era-accurate logos (``1989-90`` → ``1989``)."""
    for candidate in (
        (getattr(row, "season_label", None) or "").strip(),
        history_all_star_season_label(row),
    ):
        if not candidate:
            continue
        m = _LABEL_START_YEAR.match(candidate.strip())
        if m:
            return int(m.group(1))
    if row.season is not None and row.season.start_year is not None:
        return int(row.season.start_year)
    return None


def history_all_star_season_label(row: HistoryAllStar) -> str:
    """Display season: stored ``season_label``, then ``sheet_season=`` in notes, else Season label token."""
    sl = (getattr(row, "season_label", None) or "").strip()
    if sl:
        return sl
    n = (row.notes or "").strip()
    for piece in n.split(";"):
        p = piece.strip()
        if p.lower().startswith("sheet_season="):
            return p.split("=", 1)[1].strip()
    if row.season and row.season.label:
        m = _SEASON_TOKEN.search(row.season.label)
        if m:
            return m.group(1)
        return row.season.label.strip()
    return ""


def _group_start_year(rows: list[HistoryAllStar]) -> int:
    ys: list[int] = []
    for r in rows:
        if r.season and r.season.start_year is not None:
            ys.append(int(r.season.start_year))
    return max(ys) if ys else 0


def build_history_all_stars_bundle(session: Session, selected_season: str | None) -> dict[str, Any]:
    """Season dropdown + first/second team rows for the League History page."""
    rows = list(
        session.scalars(
            select(HistoryAllStar)
            .options(
                joinedload(HistoryAllStar.season),
                joinedload(HistoryAllStar.player),
                joinedload(HistoryAllStar.team),
            )
            .join(HistoryAllStar.season)
            .order_by(
                Season.start_year.asc(),
                HistoryAllStar.team_rank.asc(),
                HistoryAllStar.slot.asc(),
            )
        ).all()
    )
    if not rows:
        return {
            "season_labels": [],
            "selected": None,
            "first_team": [],
            "second_team": [],
            "display_title": None,
        }

    by_label: dict[str, list[HistoryAllStar]] = {}
    for r in rows:
        lab = history_all_star_season_label(r)
        if not lab:
            continue
        by_label.setdefault(lab, []).append(r)

    season_labels = sorted(
        by_label.keys(),
        key=lambda lab: (_group_start_year(by_label[lab]), lab),
        reverse=True,
    )

    sel = (selected_season or "").strip()
    if sel not in by_label:
        sel = season_labels[0] if season_labels else None

    pool = by_label.get(sel or "", [])
    first_team = sorted([r for r in pool if r.team_rank == 1], key=lambda r: r.slot)
    second_team = sorted([r for r in pool if r.team_rank == 2], key=lambda r: r.slot)

    display_title = f"BOWL — {sel} Season" if sel else None

    return {
        "season_labels": season_labels,
        "selected": sel,
        "first_team": first_team,
        "second_team": second_team,
        "display_title": display_title,
    }
