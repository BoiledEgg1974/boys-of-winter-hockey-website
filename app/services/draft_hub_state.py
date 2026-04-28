"""Draft Hub: go-live, clock, picks, auto-queue, completion + grades."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.draft_hub_eligibility import (
    DraftEligibilityParams,
    board_ranks_map,
    eligible_players_ordered,
)
from app.site_models import GmLeagueMembership, LeagueDraft, LeagueDraftPick, LeagueDraftQueueItem, LeagueDraftSlot


def utcnow_naive() -> datetime:
    return datetime.utcnow()


def params_from_draft_row(draft: LeagueDraft) -> DraftEligibilityParams:
    return DraftEligibilityParams(
        timeline_year=int(draft.timeline_year),
        min_age_years=int(draft.min_age_years),
        min_anchor_month=int(draft.min_anchor_month),
        min_anchor_day=int(draft.min_anchor_day),
        max_age_years=int(draft.max_age_years),
        max_anchor_month=int(draft.max_anchor_month),
        max_anchor_day=int(draft.max_anchor_day),
    )


def draft_eligibility_params(draft: LeagueDraft) -> DraftEligibilityParams:
    return params_from_draft_row(draft)


def slots_ordered(session: Session, draft_id: int) -> list[LeagueDraftSlot]:
    return list(
        session.scalars(
            select(LeagueDraftSlot)
            .where(LeagueDraftSlot.league_draft_id == draft_id)
            .order_by(LeagueDraftSlot.overall_pick.asc())
        ).all()
    )


def picked_player_ids(session: Session, draft_id: int) -> set[int]:
    rows = session.scalars(select(LeagueDraftPick.player_id).where(LeagueDraftPick.league_draft_id == draft_id)).all()
    return {int(x) for x in rows}


def featured_draft(session: Session, league_slug: str) -> LeagueDraft | None:
    live = session.scalar(
        select(LeagueDraft)
        .where(LeagueDraft.league_slug == league_slug, LeagueDraft.status == "live")
        .order_by(LeagueDraft.id.desc())
        .limit(1)
    )
    if live:
        return live
    setup = session.scalar(
        select(LeagueDraft)
        .where(LeagueDraft.league_slug == league_slug, LeagueDraft.status == "setup")
        .order_by(LeagueDraft.id.desc())
        .limit(1)
    )
    if setup:
        return setup
    return session.scalar(
        select(LeagueDraft)
        .where(LeagueDraft.league_slug == league_slug, LeagueDraft.status == "completed")
        .order_by(LeagueDraft.id.desc())
        .limit(1)
    )


def gm_user_ids_for_team(session: Session, league_slug: str, team_id: int) -> list[int]:
    rows = session.scalars(
        select(GmLeagueMembership.user_id)
        .where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(team_id),
            GmLeagueMembership.status == "active",
        )
        .order_by(GmLeagueMembership.user_id.asc())
    ).all()
    return [int(u) for u in rows]


def _pick_row_for_overall(session: Session, draft_id: int, overall: int) -> LeagueDraftPick | None:
    return session.scalar(
        select(LeagueDraftPick).where(
            LeagueDraftPick.league_draft_id == draft_id,
            LeagueDraftPick.overall_pick == overall,
        )
    )


def sync_current_slot_and_clock(session: Session, draft: LeagueDraft) -> None:
    """Move index past forfeited / already-picked slots; start clock when a pick is needed."""
    slots = slots_ordered(session, draft.id)
    if not slots:
        draft.status = "completed"
        draft.pick_started_at = None
        draft.pick_deadline_at = None
        draft.awaiting_admin_resolution = False
        return
    if draft.awaiting_admin_resolution:
        draft.pick_deadline_at = None
        return

    while draft.current_slot_index < len(slots):
        slot = slots[draft.current_slot_index]
        if slot.forfeited:
            draft.current_slot_index += 1
            continue
        if _pick_row_for_overall(session, draft.id, slot.overall_pick):
            draft.current_slot_index += 1
            continue
        now = utcnow_naive()
        if draft.pick_deadline_at is None or draft.awaiting_admin_resolution:
            draft.pick_started_at = now
            draft.pick_deadline_at = now + timedelta(seconds=int(draft.timer_seconds))
            draft.deadline_extended_for_slot = False
        draft.awaiting_admin_resolution = False
        return

    _finalize_draft_if_done(session, draft, slots)


def _finalize_draft_if_done(session: Session, draft: LeagueDraft, slots: list[LeagueDraftSlot]) -> None:
    needed = [s for s in slots if not s.forfeited]
    picks = list(session.scalars(select(LeagueDraftPick).where(LeagueDraftPick.league_draft_id == draft.id)).all())
    if len(picks) < len(needed):
        return
    draft.status = "completed"
    draft.pick_started_at = None
    draft.pick_deadline_at = None
    draft.awaiting_admin_resolution = False
    draft.completed_summary_json = json.dumps(compute_winners_losers(session, draft))


def compute_winners_losers(session: Session, draft: LeagueDraft) -> dict[str, Any]:
    """Heuristic team surplus from board ranks at go-live vs pick order."""
    board_raw = draft.board_ranks_json or "{}"
    try:
        board: dict[str, int] = {k: int(v) for k, v in json.loads(board_raw).items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        board = {}

    picks = list(
        session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.asc())
        ).all()
    )
    if not picks or not board:
        return {"teams": [], "top_winners": [], "top_losers": [], "note": "Insufficient board data."}

    n_pool = max(board.values()) if board else 1
    num_slots = len(picks)
    team_surplus: dict[int, float] = {}
    for pk in picks:
        pid = str(int(pk.player_id))
        br = board.get(pid)
        if br is None:
            continue
        expected = max(1, round(n_pool * int(pk.overall_pick) / (num_slots + 1)))
        surplus = float(expected - br)
        team_surplus[pk.team_id] = team_surplus.get(pk.team_id, 0.0) + surplus

    ranked = sorted(team_surplus.items(), key=lambda kv: kv[1], reverse=True)
    teams_out = [{"team_id": tid, "surplus": round(s, 2)} for tid, s in ranked]
    winners = [{"team_id": tid, "surplus": round(s, 2)} for tid, s in ranked[:3]]
    losers_asc = sorted(team_surplus.items(), key=lambda kv: kv[1])
    losers = [{"team_id": tid, "surplus": round(s, 2)} for tid, s in losers_asc[:3]]
    return {"teams": teams_out, "top_winners": winners, "top_losers": losers, "note": "Heuristic from ratings at go-live."}


def go_live(session: Session, draft: LeagueDraft, admin_user_id: int) -> str | None:
    if draft.status != "setup":
        return "Draft is not in setup status."
    other = session.scalar(
        select(LeagueDraft)
        .where(
            LeagueDraft.league_slug == draft.league_slug,
            LeagueDraft.status == "live",
            LeagueDraft.id != draft.id,
        )
        .limit(1)
    )
    if other:
        return "Another draft is already live for this site. Complete or pause it first."
    slots = slots_ordered(session, draft.id)
    if not slots:
        return "Add draft order slots before going live."
    params = draft_eligibility_params(draft)
    players = eligible_players_ordered(session, draft.league_slug, params)
    if not players:
        return "No eligible players for this draft (check age rules and pool)."
    draft.board_ranks_json = json.dumps(board_ranks_map(players))
    draft.status = "live"
    draft.current_slot_index = 0
    draft.awaiting_admin_resolution = False
    draft.deadline_extended_for_slot = False
    draft.pick_started_at = None
    draft.pick_deadline_at = None
    sync_current_slot_and_clock(session, draft)
    return None


def process_tick(session: Session, draft: LeagueDraft) -> None:
    if draft.status != "live" or draft.awaiting_admin_resolution:
        return
    ddl = draft.pick_deadline_at
    if ddl is None:
        sync_current_slot_and_clock(session, draft)
        return
    if utcnow_naive() <= ddl:
        return

    slots = slots_ordered(session, draft.id)
    if draft.current_slot_index >= len(slots):
        _finalize_draft_if_done(session, draft, slots)
        return
    slot = slots[draft.current_slot_index]
    if slot.forfeited:
        sync_current_slot_and_clock(session, draft)
        return

    picked_ids = picked_player_ids(session, draft.id)
    params = draft_eligibility_params(draft)
    eligible_ordered = eligible_players_ordered(session, draft.league_slug, params)
    eligible_ids = {p.id for p in eligible_ordered} - picked_ids

    auto_player_id: int | None = None
    auto_user_id: int | None = None
    for uid in gm_user_ids_for_team(session, draft.league_slug, slot.team_id):
        qrows = list(
            session.scalars(
                select(LeagueDraftQueueItem)
                .where(
                    LeagueDraftQueueItem.league_draft_id == draft.id,
                    LeagueDraftQueueItem.user_id == uid,
                )
                .order_by(LeagueDraftQueueItem.sort_order.asc(), LeagueDraftQueueItem.id.asc())
            ).all()
        )
        for qi in qrows:
            if int(qi.player_id) in eligible_ids:
                auto_player_id = int(qi.player_id)
                auto_user_id = uid
                break
        if auto_player_id is not None:
            break

    if auto_player_id is not None:
        err = record_pick(session, draft, auto_player_id, auto_user_id, "auto_queue")
        if not err:
            draft.deadline_extended_for_slot = False
            slots2 = slots_ordered(session, draft.id)
            _finalize_draft_if_done(session, draft, slots2)
        return

    if not draft.deadline_extended_for_slot:
        base = draft.pick_deadline_at or utcnow_naive()
        draft.pick_deadline_at = base + timedelta(seconds=int(draft.empty_queue_timer_seconds))
        draft.deadline_extended_for_slot = True
        return

    draft.awaiting_admin_resolution = True
    draft.pick_deadline_at = None


def _remove_player_from_all_queues(session: Session, draft_id: int, player_id: int) -> None:
    for row in session.scalars(select(LeagueDraftQueueItem).where(LeagueDraftQueueItem.league_draft_id == draft_id)).all():
        if int(row.player_id) == int(player_id):
            session.delete(row)


def record_pick(
    session: Session,
    draft: LeagueDraft,
    player_id: int,
    user_id: int | None,
    source: str,
) -> str | None:
    if draft.status != "live":
        return "Draft is not live."
    if draft.awaiting_admin_resolution and source != "admin":
        return "Waiting for commissioner to resolve this pick."
    slots = slots_ordered(session, draft.id)
    if draft.current_slot_index >= len(slots):
        return "No active pick slot."
    slot = slots[draft.current_slot_index]
    if slot.forfeited:
        return "Current slot is forfeited."
    if _pick_row_for_overall(session, draft.id, slot.overall_pick):
        return "Pick already recorded for this slot."
    picked = picked_player_ids(session, draft.id)
    if int(player_id) in picked:
        return "Player was already drafted."
    params = draft_eligibility_params(draft)
    eligible_ordered = eligible_players_ordered(session, draft.league_slug, params)
    eligible_ids = {p.id for p in eligible_ordered} - picked
    if int(player_id) not in eligible_ids:
        return "Player is not eligible for this draft."
    if source == "gm":
        if user_id is None:
            return "Not authenticated."
        mids = gm_user_ids_for_team(session, draft.league_slug, slot.team_id)
        if int(user_id) not in mids:
            return "You are not the GM on the clock for this team."
        ddl = draft.pick_deadline_at
        if ddl is not None and utcnow_naive() > ddl:
            return "Pick timer has expired; use the queue or ask the commissioner."

    pk = LeagueDraftPick(
        league_draft_id=draft.id,
        overall_pick=int(slot.overall_pick),
        round=int(slot.round),
        team_id=int(slot.team_id),
        player_id=int(player_id),
        source=source,
        picked_by_user_id=int(user_id) if user_id is not None else None,
    )
    session.add(pk)
    _remove_player_from_all_queues(session, draft.id, int(player_id))
    draft.current_slot_index += 1
    draft.deadline_extended_for_slot = False
    draft.awaiting_admin_resolution = False
    sync_current_slot_and_clock(session, draft)
    slots2 = slots_ordered(session, draft.id)
    _finalize_draft_if_done(session, draft, slots2)
    return None


def undo_last_pick(session: Session, draft: LeagueDraft) -> str | None:
    if draft.status != "live":
        return "Can only undo during a live draft."
    picks = list(
        session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.desc())
        ).all()
    )
    if not picks:
        return "No picks to undo."
    last = picks[0]
    session.delete(last)
    slots = slots_ordered(session, draft.id)
    for i, s in enumerate(slots):
        if int(s.overall_pick) == int(last.overall_pick):
            draft.current_slot_index = i
            break
    draft.awaiting_admin_resolution = False
    draft.deadline_extended_for_slot = False
    sync_current_slot_and_clock(session, draft)
    return None


def resolve_admin_pick(
    session: Session,
    draft: LeagueDraft,
    player_id: int,
    admin_user_id: int,
) -> str | None:
    if draft.status != "live":
        return "Draft is not live."
    return record_pick(session, draft, player_id, admin_user_id, "admin")
