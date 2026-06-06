"""Draft Hub tracker summary: countdown, first pick, team pick breakdown."""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Game, Team
from app.services.draft_hub_order import main_league_standings_worst_to_best, resolve_prior_season_for_draft
from app.services.draft_pick_ownership import list_draft_pick_ownership_year_panels
from app.services.draft_pick_values import (
    perri_pick_value_for_asset,
    pick_value_attribution,
)
from app.site_models import DraftPickOwnershipYear, LeagueDraft, LeagueDraftSlot, TradeMarketDraftPickOwnership


def _team_color(tm: Team | None) -> str | None:
    raw = (getattr(tm, "primary_color", None) or "").strip()
    if not raw.startswith("#") or len(raw) not in (4, 7):
        return None
    if not all(ch in "0123456789abcdefABCDEF" for ch in raw[1:]):
        return None
    return raw


def _latest_game_date(league_session: Session) -> date | None:
    return league_session.scalar(
        select(Game.game_date)
        .where(Game.game_date.isnot(None))
        .order_by(Game.game_date.desc(), Game.id.desc())
        .limit(1)
    )


def _active_ownership_panel(
    site_session: Session, *, league_slug: str
) -> DraftPickOwnershipYear | None:
    panels = list_draft_pick_ownership_year_panels(site_session, league_slug=league_slug)
    active = [p for p in panels if str(p.status or "active") != "completed"]
    if not active:
        return None
    return sorted(active, key=lambda p: (int(p.display_order), int(p.draft_year)))[0]


def _round1_position_by_team_id(
    league_session: Session, *, draft_year: int
) -> dict[int, int]:
    season = resolve_prior_season_for_draft(league_session, draft_year=int(draft_year))
    if season is None:
        return {}
    standings = main_league_standings_worst_to_best(league_session, season)
    out: dict[int, int] = {}
    for idx, row in enumerate(standings, start=1):
        if row.team is None:
            continue
        out[int(row.team.id)] = int(idx)
    return out


def _ownership_rows_for_year(
    site_session: Session,
    *,
    league_slug: str,
    draft_year: int,
    round_count: int,
) -> list[TradeMarketDraftPickOwnership]:
    return list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership)
            .join(
                DraftPickOwnershipYear,
                and_(
                    DraftPickOwnershipYear.league_slug == TradeMarketDraftPickOwnership.league_slug,
                    DraftPickOwnershipYear.draft_year == TradeMarketDraftPickOwnership.draft_year,
                    DraftPickOwnershipYear.status != "completed",
                ),
            )
            .where(
                TradeMarketDraftPickOwnership.league_slug == league_slug,
                TradeMarketDraftPickOwnership.draft_year == int(draft_year),
                TradeMarketDraftPickOwnership.round <= int(round_count),
            )
        ).all()
    )


def _slot_rows_for_draft(site_session: Session, draft_id: int) -> list[LeagueDraftSlot]:
    return list(
        site_session.scalars(
            select(LeagueDraftSlot)
            .where(LeagueDraftSlot.league_draft_id == int(draft_id))
            .order_by(LeagueDraftSlot.overall_pick.asc())
        ).all()
    )


def _teams_tied_at_value(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    best = max(float(r.get(key) or 0) for r in rows)
    return [r for r in rows if abs(float(r.get(key) or 0) - best) < 0.001]


def _featured_draft_is_current_for_tracker(
    featured_draft: LeagueDraft | None,
    panel: DraftPickOwnershipYear | None,
) -> bool:
    if featured_draft is None:
        return False
    status = str(featured_draft.status or "").strip().lower()
    if status != "completed":
        return True
    if panel is None:
        return True
    try:
        return int(featured_draft.timeline_year) >= int(panel.draft_year)
    except (TypeError, ValueError):
        return False


def build_draft_hub_tracker(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    featured_draft: LeagueDraft | None,
    team_by_id: dict[int, Team],
    team_logo_url: Callable[[Team | None, LeagueDraft | None], str | None],
    team_page_url: Callable[[Team], str],
    draft_hub_url: Callable[[], str],
    draft_archive_url: Callable[[], str],
    draft_archive_one_url: Callable[[int], str],
) -> dict[str, Any]:
    slug = str(league_slug or "").strip()
    attr = pick_value_attribution()
    panel = _active_ownership_panel(site_session, league_slug=slug)
    use_featured_draft = _featured_draft_is_current_for_tracker(featured_draft, panel)
    tracker_draft = featured_draft if use_featured_draft else None
    draft_year = int(
        (getattr(tracker_draft, "timeline_year", None) if tracker_draft else None)
        or (panel.draft_year if panel else 0)
        or 0
    )
    round_count = int(panel.round_count) if panel else 7
    round_count = max(1, min(7, round_count))

    slots = (
        _slot_rows_for_draft(site_session, int(tracker_draft.id))
        if tracker_draft and tracker_draft.id
        else []
    )
    use_slots = bool(slots)
    r1_pos = _round1_position_by_team_id(league_session, draft_year=draft_year) if draft_year else {}

    pick_assets: list[dict[str, Any]] = []
    if use_slots:
        for slot in slots:
            if slot.forfeited or slot.team_id is None:
                continue
            orig_tid = int(slot.original_team_id or slot.team_id)
            pick_assets.append(
                {
                    "owner_team_id": int(slot.team_id),
                    "round": int(slot.round),
                    "overall_pick": int(slot.overall_pick),
                    "original_team_id": orig_tid,
                }
            )
    elif panel and draft_year:
        for row in _ownership_rows_for_year(
            site_session, league_slug=slug, draft_year=draft_year, round_count=round_count
        ):
            if row.owner_team_id is None:
                continue
            orig_tid = int(row.original_team_id or row.owner_team_id)
            pick_assets.append(
                {
                    "owner_team_id": int(row.owner_team_id),
                    "round": int(row.round),
                    "overall_pick": None,
                    "original_team_id": orig_tid,
                    "original_round1_position": r1_pos.get(orig_tid),
                }
            )

    team_rows_map: dict[int, dict[str, Any]] = {}
    for tid, tm in team_by_id.items():
        team_rows_map[int(tid)] = {
            "team_id": int(tid),
            "team_name": tm.full_display_name() if tm else f"Team {tid}",
            "team_abbr": (tm.abbreviation or "").strip() if tm else "",
            "team_slug": (tm.slug or "").strip() if tm else "",
            "team_color": _team_color(tm),
            "team_logo_url": team_logo_url(tm, tracker_draft),
            "team_page_url": team_page_url(tm) if tm else "",
            "pick_count": 0,
            "pick_value": 0.0,
            "picks_by_round": {str(r): 0 for r in range(1, 8)},
            "value_by_round": {str(r): 0.0 for r in range(1, 8)},
            "picks": [],
        }

    for asset in pick_assets:
        owner_tid = int(asset["owner_team_id"])
        rnd = int(asset["round"])
        if owner_tid not in team_rows_map:
            continue
        orig_tid = int(asset.get("original_team_id") or owner_tid)
        overall = asset.get("overall_pick")
        value = perri_pick_value_for_asset(
            overall_pick=int(overall) if overall is not None else None,
            round_no=rnd,
            original_round1_position=r1_pos.get(orig_tid),
            order_known=overall is not None,
        )
        row = team_rows_map[owner_tid]
        row["pick_count"] += 1
        row["pick_value"] = round(float(row["pick_value"]) + float(value), 2)
        rk = str(rnd)
        if rk in row["picks_by_round"]:
            row["picks_by_round"][rk] += 1
            row["value_by_round"][rk] = round(float(row["value_by_round"][rk]) + float(value), 2)
        row["picks"].append(
            {
                "round": rnd,
                "overall_pick": int(overall) if overall is not None else None,
                "value": float(value),
                "original_team_id": orig_tid,
            }
        )

    team_rows = sorted(
        team_rows_map.values(),
        key=lambda r: str(r.get("team_name") or "").casefold(),
    )

    first_pick: dict[str, Any] | None = None
    if use_slots:
        first_slot = next((s for s in slots if not s.forfeited), None)
        if first_slot and first_slot.team_id:
            tm = team_by_id.get(int(first_slot.team_id))
            first_pick = {
                "team_id": int(first_slot.team_id),
                "team_name": tm.full_display_name() if tm else f"Team {first_slot.team_id}",
                "team_abbr": (tm.abbreviation or "").strip() if tm else "",
                "team_logo_url": team_logo_url(tm, featured_draft),
                "overall_pick": int(first_slot.overall_pick),
            }
    elif r1_pos:
        first_asset = next(
            (
                a
                for a in pick_assets
                if int(a.get("round") or 0) == 1
                and int(a.get("original_round1_position") or 0) == 1
            ),
            None,
        )
        first_owner_tid = int(first_asset["owner_team_id"]) if first_asset else None
        if first_owner_tid is not None:
            tm = team_by_id.get(int(first_owner_tid))
            first_pick = {
                "team_id": int(first_owner_tid),
                "team_name": tm.full_display_name() if tm else f"Team {first_owner_tid}",
                "team_abbr": (tm.abbreviation or "").strip() if tm else "",
                "team_logo_url": team_logo_url(tm, tracker_draft),
                "overall_pick": 1,
            }

    owned_rows = [r for r in team_rows if int(r.get("pick_count") or 0) > 0]
    max_picks = max((int(r.get("pick_count") or 0) for r in owned_rows), default=0)
    min_picks = min((int(r.get("pick_count") or 0) for r in owned_rows), default=0) if owned_rows else 0
    most_picks = [r for r in owned_rows if int(r.get("pick_count") or 0) == max_picks] if max_picks else []
    fewest_picks = [r for r in owned_rows if int(r.get("pick_count") or 0) == min_picks] if owned_rows else []
    highest_value = _teams_tied_at_value(owned_rows, "pick_value")

    current_game_date = _latest_game_date(league_session)
    draft_target_date: date | None = None
    if tracker_draft and tracker_draft.scheduled_start_at:
        draft_target_date = tracker_draft.scheduled_start_at.date()
    elif draft_year:
        draft_target_date = date(int(draft_year), 6, 26)

    days_until: int | None = None
    countdown_label = "Draft date TBD"
    if current_game_date and draft_target_date:
        days_until = (draft_target_date - current_game_date).days
        if days_until > 0:
            countdown_label = f"{days_until} day{'s' if days_until != 1 else ''}"
        elif days_until == 0:
            countdown_label = "Draft day"
        else:
            countdown_label = "Draft underway"

    status_label = "Pending setup"
    if tracker_draft:
        st = str(tracker_draft.status or "").strip().lower()
        if st == "live":
            status_label = "Live now"
        elif st == "completed":
            status_label = "Completed"
        elif st == "setup":
            status_label = "Pending setup"
    elif panel is not None:
        status_label = "Upcoming"

    drafts_all = list(
        site_session.scalars(
            select(LeagueDraft)
            .where(LeagueDraft.league_slug == slug)
            .order_by(LeagueDraft.id.desc())
        ).all()
    )
    pending_drafts = [d for d in drafts_all if str(d.status or "") == "setup"]
    live_drafts = [d for d in drafts_all if str(d.status or "") == "live"]
    hub_links: list[dict[str, Any]] = []
    if live_drafts:
        d = live_drafts[0]
        hub_links.append(
            {
                "label": f"Live: {d.name}",
                "url": draft_hub_url(),
                "status": "live",
                "draft_id": int(d.id),
            }
        )
    elif featured_draft and str(featured_draft.status or "") in {"setup", "live"}:
        hub_links.append(
            {
                "label": featured_draft.name,
                "url": draft_hub_url(),
                "status": str(featured_draft.status or "setup"),
                "draft_id": int(featured_draft.id),
            }
        )
    if pending_drafts:
        for d in pending_drafts[:3]:
            if featured_draft and int(d.id) == int(featured_draft.id):
                continue
            hub_links.append(
                {
                    "label": f"Upcoming: {d.name}",
                    "url": draft_hub_url(),
                    "status": "setup",
                    "draft_id": int(d.id),
                }
            )
    hub_links.append({"label": "Draft Archive", "url": draft_archive_url(), "status": "archive"})

    title_year = draft_year or (int(featured_draft.timeline_year) if featured_draft else None)
    return {
        "title": f"{title_year} Draft Tracker" if title_year else "Draft Tracker",
        "subtitle": "Everything you need to know leading up to the next league draft.",
        "draft_year": title_year,
        "status_label": status_label,
        "countdown_label": countdown_label,
        "days_until": days_until,
        "current_game_date": current_game_date.isoformat() if current_game_date else None,
        "draft_target_date": draft_target_date.isoformat() if draft_target_date else None,
        "scheduled_start_at": (
            tracker_draft.scheduled_start_at.isoformat()
            if tracker_draft and tracker_draft.scheduled_start_at
            else None
        ),
        "first_pick": first_pick,
        "most_picks": {
            "count": max_picks,
            "teams": [
                {
                    "team_id": int(t["team_id"]),
                    "team_name": t.get("team_name"),
                    "team_abbr": t.get("team_abbr"),
                    "team_logo_url": t.get("team_logo_url"),
                }
                for t in most_picks
            ],
        },
        "fewest_picks": {
            "count": min_picks if owned_rows else 0,
            "teams": [
                {
                    "team_id": int(t["team_id"]),
                    "team_name": t.get("team_name"),
                    "team_abbr": t.get("team_abbr"),
                    "team_logo_url": t.get("team_logo_url"),
                }
                for t in fewest_picks
            ],
        },
        "highest_pick_value": {
            "value": round(float(highest_value[0]["pick_value"]), 1) if highest_value else 0.0,
            "teams": [
                {
                    "team_id": int(t["team_id"]),
                    "team_name": t.get("team_name"),
                    "team_abbr": t.get("team_abbr"),
                    "team_logo_url": t.get("team_logo_url"),
                }
                for t in highest_value
            ],
        },
        "team_breakdown": team_rows,
        "round_count": round_count,
        "pick_value_attribution": {
            "text": attr.text,
            "calculator_url": attr.calculator_url,
            "methodology_url": attr.methodology_url,
        },
        "hub_links": hub_links,
        "has_pick_data": bool(pick_assets),
    }
