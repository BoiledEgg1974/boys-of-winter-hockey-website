"""Draft Hub: go-live, clock, picks, optional wishlist queue, completion + grades."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.draft_hub_eligibility import DraftEligibilityParams, board_ranks_map
from app.services.draft_hub_eligibility_cache import (
    eligible_id_set_for_draft,
    eligible_players_for_board,
)
from app.services.player_ratings_csv import player_positions_display_label
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


def wishlist_head_for_user(
    session: Session, draft: LeagueDraft, league_slug: str, user_id: int
) -> tuple[int | None, str | None]:
    """First still-eligible player on this user's wishlist (queue), for pick authorization UI."""
    picked = picked_player_ids(session, draft.id)
    params = draft_eligibility_params(draft)
    eligible_ids = eligible_id_set_for_draft(session, league_slug, params, picked)
    qrows = list(
        session.scalars(
            select(LeagueDraftQueueItem)
            .where(
                LeagueDraftQueueItem.league_draft_id == draft.id,
                LeagueDraftQueueItem.user_id == int(user_id),
            )
            .order_by(LeagueDraftQueueItem.sort_order.asc(), LeagueDraftQueueItem.id.asc())
        ).all()
    )
    for qi in qrows:
        pid = int(qi.player_id)
        if pid in eligible_ids:
            pl = session.get(Player, pid)
            name = pl.full_name if pl else f"Player #{pid}"
            return pid, name
    return None, None


def wishlist_items_for_team(
    session: Session, draft: LeagueDraft, league_slug: str, team_id: int
) -> list[dict[str, object]]:
    """Ordered wishlist rows for all GMs on a franchise (eligible players only)."""
    picked = picked_player_ids(session, draft.id)
    params = draft_eligibility_params(draft)
    eligible_ids = eligible_id_set_for_draft(session, league_slug, params, picked)
    uids = gm_user_ids_for_team(session, league_slug, int(team_id))
    if not uids:
        return []
    qrows = list(
        session.scalars(
            select(LeagueDraftQueueItem)
            .where(
                LeagueDraftQueueItem.league_draft_id == draft.id,
                LeagueDraftQueueItem.user_id.in_(uids),
            )
            .order_by(LeagueDraftQueueItem.sort_order.asc(), LeagueDraftQueueItem.id.asc())
        ).all()
    )
    if not qrows:
        return []
    pids = [int(x.player_id) for x in qrows]
    name_by_pid: dict[int, str] = {}
    pos_by_pid: dict[int, str] = {}
    for pl in session.scalars(select(Player).where(Player.id.in_(pids))).unique().all():
        name_by_pid[int(pl.id)] = pl.full_name or ""
        pos_by_pid[int(pl.id)] = player_positions_display_label(pl) or ""
    out: list[dict[str, object]] = []
    for qi in qrows:
        pid = int(qi.player_id)
        if pid not in eligible_ids:
            continue
        out.append(
            {
                "id": int(qi.id),
                "player_id": pid,
                "name": name_by_pid.get(pid, f"Player #{pid}"),
                "pos": pos_by_pid.get(pid, ""),
            }
        )
    return out


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
        draft.timer_paused = False
        draft.timer_paused_remaining_seconds = None
        draft.awaiting_admin_resolution = False
        return
    if draft.awaiting_admin_resolution:
        draft.pick_deadline_at = None
        return
    if getattr(draft, "timer_paused", False):
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
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    draft.awaiting_admin_resolution = False
    draft.completed_summary_json = json.dumps(compute_winners_losers(session, draft))


def compute_winners_losers(session: Session, draft: LeagueDraft) -> dict[str, Any]:
    """Average drafted-player quality per team, scored 0-100 from board rank at go-live."""
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
    team_scores: dict[int, list[float]] = {}
    for pk in picks:
        pid = str(int(pk.player_id))
        br = board.get(pid)
        if br is None:
            continue
        quality = max(0.0, (float(n_pool) - float(br) + 1.0) / float(n_pool) * 100.0)
        team_scores.setdefault(int(pk.team_id), []).append(quality)

    team_avg: dict[int, float] = {
        tid: (sum(vs) / len(vs)) for tid, vs in team_scores.items() if vs
    }
    ranked_desc = sorted(team_avg.items(), key=lambda kv: (-kv[1], kv[0]))
    ranked_asc = sorted(team_avg.items(), key=lambda kv: (kv[1], kv[0]))
    teams_out = [{"team_id": tid, "score": round(s, 1)} for tid, s in ranked_desc]
    winners = [{"team_id": tid, "score": round(s, 1)} for tid, s in ranked_desc[:3]]
    losers = [{"team_id": tid, "score": round(s, 1)} for tid, s in ranked_asc[:3]]
    return {
        "teams": teams_out,
        "top_winners": winners,
        "top_losers": losers,
        "note": "Average pick quality (100 = #1 on the board at go-live).",
    }


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
    players = eligible_players_for_board(session, draft.league_slug, params, set())
    if not players:
        return "No eligible players for this draft (check age rules and pool)."
    draft.board_ranks_json = json.dumps(board_ranks_map(players))
    draft.status = "live"
    draft.current_slot_index = 0
    draft.awaiting_admin_resolution = False
    draft.deadline_extended_for_slot = False
    draft.pick_started_at = None
    draft.pick_deadline_at = None
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    sync_current_slot_and_clock(session, draft)
    return None


def pause_draft_timer(session: Session, draft: LeagueDraft) -> str | None:
    """Pause the current live pick countdown and remember the remaining time."""
    if draft.status != "live":
        return "Draft is not live."
    if draft.awaiting_admin_resolution:
        return "Current pick already needs commissioner action."
    if getattr(draft, "timer_paused", False):
        return None
    slots = slots_ordered(session, draft.id)
    if draft.current_slot_index >= len(slots):
        return "No active pick slot."
    now = utcnow_naive()
    ddl = draft.pick_deadline_at
    remaining = int(max(1, round((ddl - now).total_seconds()))) if ddl else int(draft.timer_seconds)
    draft.timer_paused = True
    draft.timer_paused_remaining_seconds = remaining
    draft.pick_deadline_at = None
    return None


def resume_draft_timer(session: Session, draft: LeagueDraft) -> str | None:
    """Resume a paused live pick countdown or restart a commissioner-stopped pick."""
    if draft.status != "live":
        return "Draft is not live."
    slots = slots_ordered(session, draft.id)
    if draft.current_slot_index >= len(slots):
        return "No active pick slot."
    if not getattr(draft, "timer_paused", False) and not draft.awaiting_admin_resolution:
        return None
    remaining = int(draft.timer_paused_remaining_seconds or draft.timer_seconds or 120)
    remaining = max(1, remaining)
    now = utcnow_naive()
    draft.awaiting_admin_resolution = False
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    draft.pick_started_at = now
    draft.pick_deadline_at = now + timedelta(seconds=remaining)
    return None


def process_tick(session: Session, draft: LeagueDraft) -> None:
    if draft.status != "live" or draft.awaiting_admin_resolution or getattr(draft, "timer_paused", False):
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

    # Wishlist is never consumed when the pick timer expires — only explicit GM/admin picks
    # or "Auto-Complete Draft" may record from the queue. Mirror empty-queue handling: one
    # grace extension, then commissioner resolution.
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
    eligible_ids = eligible_id_set_for_draft(session, draft.league_slug, params, picked)
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
            return "Pick timer has expired; ask the commissioner to resume this pick."

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
    session.flush()
    try:
        from app.services.discord_events import (
            draft_hub_pick_discord_payload,
            enqueue_discord_event,
            team_fields_for_discord,
        )

        tm = session.get(Team, int(pk.team_id))
        pl = session.get(Player, int(pk.player_id))
        pos_lbl = (player_positions_display_label(pl) or "").strip() if pl else ""
        ply_name = pl.full_name if pl else str(pk.player_id)
        enqueue_discord_event(
            session,
            league_slug=draft.league_slug,
            event_key="draft_hub_pick_made",
            payload=draft_hub_pick_discord_payload(
                draft=draft,
                pick=pk,
                player_name=ply_name,
                player_pos=pos_lbl,
                team_fields=team_fields_for_discord(tm),
                pick_id=int(pk.id),
            ),
            created_by_user_id=int(user_id) if user_id is not None else None,
            source_type="draft_hub_pick",
            source_id=int(pk.id),
        )
    except Exception:
        pass
    _remove_player_from_all_queues(session, draft.id, int(player_id))
    draft.current_slot_index += 1
    draft.deadline_extended_for_slot = False
    draft.awaiting_admin_resolution = False
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    sync_current_slot_and_clock(session, draft)
    slots2 = slots_ordered(session, draft.id)
    _finalize_draft_if_done(session, draft, slots2)
    return None


def reassign_pick_team(
    session: Session,
    draft: LeagueDraft,
    overall_pick: int,
    new_team_id: int,
    admin_user_id: int,
) -> str | None:
    """Assign an unpicked slot to a different franchise (overall number stays the same)."""
    del admin_user_id
    if draft.status != "live":
        return "Draft is not live."
    if draft.awaiting_admin_resolution:
        return "Resolve the commissioner stop before changing slot ownership."
    slots = slots_ordered(session, draft.id)
    by_ov: dict[int, LeagueDraftSlot] = {int(s.overall_pick): s for s in slots}
    slot = by_ov.get(int(overall_pick))
    if slot is None:
        return "Invalid pick slot."
    if slot.forfeited:
        return "Cannot reassign a forfeited pick slot."
    if _pick_row_for_overall(session, draft.id, int(overall_pick)):
        return "That pick has already been made. Undo it first if you need to change ownership."
    tm = session.get(Team, int(new_team_id))
    if tm is None:
        return "Unknown team."
    if int(slot.team_id) == int(new_team_id):
        return "That slot is already assigned to this team."
    tid_old = int(slot.team_id)
    if slot.original_team_id is None:
        slot.original_team_id = tid_old
    slot.team_id = int(new_team_id)
    sync_current_slot_and_clock(session, draft)
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
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
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


def end_draft_early(session: Session, draft: LeagueDraft, admin_user_id: int) -> str | None:
    """Mark a live entry (prospect) draft completed immediately (partial picks allowed)."""
    del admin_user_id
    if draft.status != "live":
        return "Draft is not live."
    draft.status = "completed"
    draft.pick_started_at = None
    draft.pick_deadline_at = None
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    draft.awaiting_admin_resolution = False
    draft.deadline_extended_for_slot = False
    draft.completed_summary_json = json.dumps(compute_winners_losers(session, draft))
    return None


def swap_draft_slot_team_ids(
    session: Session,
    draft: LeagueDraft,
    overall_a: int,
    overall_b: int,
    admin_user_id: int,
) -> str | None:
    """Swap ``team_id`` between two unpicked slots (commissioner pick trade). ``admin_user_id`` reserved for audit hooks."""
    _ = admin_user_id
    if draft.status != "live":
        return "Draft is not live."
    if draft.awaiting_admin_resolution:
        return "Resolve the commissioner stop (or resume the clock) before trading slots."
    if int(overall_a) == int(overall_b):
        return "Choose two different overall pick numbers."
    slots = slots_ordered(session, draft.id)
    by_ov: dict[int, LeagueDraftSlot] = {int(s.overall_pick): s for s in slots}
    sa = by_ov.get(int(overall_a))
    sb = by_ov.get(int(overall_b))
    if sa is None or sb is None:
        return "Invalid overall pick number(s)."
    if sa.forfeited or sb.forfeited:
        return "Cannot trade a forfeited pick slot."
    if _pick_row_for_overall(session, draft.id, int(overall_a)) or _pick_row_for_overall(
        session, draft.id, int(overall_b)
    ):
        return "Both slots must still be unpicked (already-made picks cannot be reassigned here)."
    tid_a = int(sa.team_id)
    tid_b = int(sb.team_id)
    if tid_a == tid_b:
        return "Those picks already belong to the same team."
    # Ensure ``original_team_id`` is set so the hub can show the same "from ABBR" tag as
    # pre-trade order rows: API uses ``original_team_id or team_id``; if both were null,
    # a swap-only change would make original == current and hide the badge.
    if sa.original_team_id is None:
        sa.original_team_id = tid_a
    if sb.original_team_id is None:
        sb.original_team_id = tid_b
    sa.team_id = tid_b
    sb.team_id = tid_a
    sync_current_slot_and_clock(session, draft)
    return None


def auto_complete_draft(
    session: Session,
    draft: LeagueDraft,
    admin_user_id: int,
) -> tuple[int, str | None]:
    """Run every remaining pick automatically using each team's wishlist, then BPA.

    For each open slot:
      1. Walk the team's GM wishlist (auto-queue) and pick the highest-ranked eligible entry.
      2. If no wishlist entry is eligible, pick the best player available on the board.

    Clears any commissioner-paused / awaiting-admin state so the loop can proceed.
    Returns ``(picks_made, error_or_none)``. The caller commits / rolls back.
    """
    if draft.status != "live":
        return 0, "Draft is not live."

    # Allow the loop to run even if the clock is paused or the commissioner was holding things.
    draft.awaiting_admin_resolution = False
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None

    picks_made = 0
    # Safety: cap iterations well above realistic slot counts so a logic bug never spins forever.
    max_iters = 5000
    for _ in range(max_iters):
        if draft.status != "live":
            break
        slots = slots_ordered(session, draft.id)
        if draft.current_slot_index >= len(slots):
            _finalize_draft_if_done(session, draft, slots)
            break

        slot = slots[draft.current_slot_index]
        if slot.forfeited:
            draft.current_slot_index += 1
            continue
        if _pick_row_for_overall(session, draft.id, slot.overall_pick):
            draft.current_slot_index += 1
            continue

        picked_ids = picked_player_ids(session, draft.id)
        params = draft_eligibility_params(draft)
        eligible_remaining = eligible_players_for_board(
            session, draft.league_slug, params, picked_ids
        )
        if not eligible_remaining:
            _finalize_draft_if_done(session, draft, slots)
            break
        eligible_id_set = {p.id for p in eligible_remaining}

        chosen_pid: int | None = None
        chosen_uid: int | None = None
        for uid in gm_user_ids_for_team(session, draft.league_slug, slot.team_id):
            qrows = list(
                session.scalars(
                    select(LeagueDraftQueueItem)
                    .where(
                        LeagueDraftQueueItem.league_draft_id == draft.id,
                        LeagueDraftQueueItem.user_id == uid,
                    )
                    .order_by(
                        LeagueDraftQueueItem.sort_order.asc(),
                        LeagueDraftQueueItem.id.asc(),
                    )
                ).all()
            )
            for qi in qrows:
                if int(qi.player_id) in eligible_id_set:
                    chosen_pid = int(qi.player_id)
                    chosen_uid = uid
                    break
            if chosen_pid is not None:
                break

        if chosen_pid is None:
            chosen_pid = eligible_remaining[0].id
            chosen_uid = admin_user_id
            source = "admin"
        else:
            source = "auto_queue"

        err = record_pick(session, draft, chosen_pid, chosen_uid, source)
        if err:
            return picks_made, err
        picks_made += 1
    else:
        return picks_made, "Auto-complete iteration cap reached. Run again to continue."

    return picks_made, None
