"""Team Prospects tab helpers: draft details and 1-year develop rate."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Draft, DraftPick, PlayerRatingSnapshot
from app.services.draft_history import nhl_bowl_draft_clause


def ordinal_suffix(n: int) -> str:
    n = abs(int(n))
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_draft_details(
    *,
    team_name: str | None,
    draft_year: int | None,
    overall_pick: int | None,
) -> dict[str, str | None]:
    """Two-line draft detail cell for the Prospects table."""
    if draft_year is None and overall_pick is None and not (team_name or "").strip():
        return {"team_line": "Undrafted", "pick_line": None}
    team_line = (team_name or "").strip() or "Unknown team"
    if draft_year is not None and overall_pick is not None:
        pick_line = f"{int(draft_year)} · {ordinal_suffix(int(overall_pick))} Overall"
    elif draft_year is not None:
        pick_line = str(int(draft_year))
    elif overall_pick is not None:
        pick_line = f"{ordinal_suffix(int(overall_pick))} Overall"
    else:
        pick_line = None
    return {"team_line": team_line, "pick_line": pick_line}


def draft_details_by_player_id(
    session: Session,
    player_ids: list[int],
) -> dict[int, dict[str, str | None]]:
    """Map player_id → draft detail lines (earliest NHL/BOWL pick preferred)."""
    if not player_ids:
        return {}
    picks = session.scalars(
        select(DraftPick)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .options(joinedload(DraftPick.team), joinedload(DraftPick.draft))
        .where(DraftPick.player_id.in_(player_ids))
        .where(nhl_bowl_draft_clause())
        .order_by(
            DraftPick.draft_year.asc().nulls_last(),
            DraftPick.overall_pick.asc().nulls_last(),
            DraftPick.id.asc(),
        )
    ).unique().all()

    out: dict[int, dict[str, str | None]] = {}
    for pick in picks:
        pid = int(pick.player_id) if pick.player_id is not None else None
        if pid is None or pid in out:
            continue
        team = pick.team
        team_name = None
        if team is not None:
            team_name = (team.name or team.city or team.abbreviation or "").strip() or None
        out[pid] = format_draft_details(
            team_name=team_name,
            draft_year=int(pick.draft_year) if pick.draft_year is not None else None,
            overall_pick=int(pick.overall_pick) if pick.overall_pick is not None else None,
        )
    for pid in player_ids:
        if int(pid) not in out:
            out[int(pid)] = format_draft_details(
                team_name=None, draft_year=None, overall_pick=None
            )
    return out


def develop_rate_from_snapshots(
    snapshots: list[PlayerRatingSnapshot] | list[object],
) -> tuple[int | None, float | None]:
    """Return (OVR point delta, percent change vs starting OVR) over the snapshot window."""
    scores: list[float] = []
    for snap in snapshots:
        ovr = getattr(snap, "overall_score", None)
        if ovr is None:
            continue
        scores.append(float(ovr))
    if len(scores) < 2:
        return None, None
    start = scores[0]
    end = scores[-1]
    delta = int(round(end - start))
    if start <= 0:
        return delta, None
    pct = round(((end - start) / start) * 100.0, 1)
    return delta, float(pct)


def develop_rates_by_player_id(
    session: Session,
    player_ids: list[int],
    *,
    within_days: int = 365,
) -> dict[int, tuple[int | None, float | None]]:
    """Batch 1YR develop rate (OVR delta + percent) for many players."""
    if not player_ids:
        return {}
    cutoff = datetime.utcnow() - timedelta(days=max(1, within_days))
    rows = session.scalars(
        select(PlayerRatingSnapshot)
        .where(
            PlayerRatingSnapshot.player_id.in_(player_ids),
            PlayerRatingSnapshot.snapshot_at >= cutoff,
        )
        .order_by(
            PlayerRatingSnapshot.player_id.asc(),
            PlayerRatingSnapshot.snapshot_at.asc(),
            PlayerRatingSnapshot.id.asc(),
        )
    ).all()
    by_player: dict[int, list[PlayerRatingSnapshot]] = {}
    for snap in rows:
        by_player.setdefault(int(snap.player_id), []).append(snap)
    return {
        int(pid): develop_rate_from_snapshots(by_player.get(int(pid), []))
        for pid in player_ids
    }


def format_develop_rate(delta: int | None, pct: float | None = None) -> dict[str, Any]:
    """Format develop rate for display; prefers percent when available."""
    if delta is None and pct is None:
        return {"value": None, "display": "—", "kind": "none", "delta": None, "pct": None}
    if pct is not None:
        if pct > 0:
            kind = "up"
        elif pct < 0:
            kind = "down"
        else:
            kind = "flat"
        if abs(pct - round(pct)) < 0.05:
            display = f"{int(round(pct)):+d}%"
        else:
            display = f"{pct:+.1f}%"
        return {
            "value": float(pct),
            "display": display,
            "kind": kind,
            "delta": delta,
            "pct": float(pct),
        }
    if delta is not None:
        if delta > 0:
            return {"value": float(delta), "display": f"+{delta}", "kind": "up", "delta": delta, "pct": None}
        if delta < 0:
            return {"value": float(delta), "display": str(delta), "kind": "down", "delta": delta, "pct": None}
        return {"value": 0.0, "display": "0", "kind": "flat", "delta": 0, "pct": 0.0}
    return {"value": None, "display": "—", "kind": "none", "delta": None, "pct": None}
