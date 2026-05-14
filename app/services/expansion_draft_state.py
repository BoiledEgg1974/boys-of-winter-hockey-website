"""Expansion Draft Hub: slots, eligibility, clock, picks."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, Prospect, Team
from app.services.draft_hub_eligibility import age_as_of
from app.services.roster_team import organization_main_team, organization_main_team_from_maps
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.player_ratings_csv import (
    ELIGIBLE_POSITION_DISPLAY_MIN_RATING,
    eligible_positions_from_ratings_row,
    get_player_ratings_row,
)
from app.site_models import (
    GmLeagueMembership,
    LeagueExpansionDraft,
    LeagueExpansionDraftEligiblePlayer,
    LeagueExpansionDraftPick,
    LeagueExpansionDraftSlot,
)

_FORWARD_TOKENS = frozenset({"LW", "C", "RW"})
_DEFENSE_TOKENS = frozenset({"LD", "RD"})
_EXPANSION_BOARD_MIN_AGE = 21


def _expansion_board_age_ok(pl: Player) -> bool:
    """Players under 21 (league ``season_age_reference_date``) never appear on the expansion board."""
    ag = age_as_of(pl.birth_date, season_age_reference_date(get_current_season()))
    return ag is not None and ag >= _EXPANSION_BOARD_MIN_AGE


def _rights_holder_team_id_for_losses(session: Session, pl: Player) -> int | None:
    """BOWL org for max-loss rules; falls back to ``current_team_id``."""
    org = organization_main_team(session, pl)
    if org is not None:
        return int(org.id)
    if pl.current_team_id is not None:
        return int(pl.current_team_id)
    return None


def utcnow_naive() -> datetime:
    return datetime.utcnow()


def expansion_team_order(draft: LeagueExpansionDraft) -> list[int]:
    try:
        raw = json.loads(draft.expansion_team_order_json or "[]")
        return [int(x) for x in raw if str(x).strip().lstrip("-").isdigit()]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def exempt_team_ids(draft: LeagueExpansionDraft) -> set[int]:
    try:
        raw = json.loads(draft.exempt_team_ids_json or "[]")
        return {int(x) for x in raw if str(x).strip().lstrip("-").isdigit()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


def set_expansion_team_order(draft: LeagueExpansionDraft, team_ids: list[int]) -> None:
    draft.expansion_team_order_json = json.dumps([int(x) for x in team_ids])


def set_exempt_team_ids(draft: LeagueExpansionDraft, team_ids: set[int]) -> None:
    draft.exempt_team_ids_json = json.dumps(sorted(int(x) for x in team_ids))


def expansion_franchise_ids_sorted(draft: LeagueExpansionDraft) -> list[int]:
    """Unique expansion franchise team ids, sorted for stable rotation."""
    raw = expansion_team_order(draft)
    return sorted({int(x) for x in raw})


def phase_pick_order(franchise_ids: list[int], first_team_id: int | None) -> list[int]:
    """Rotate ``franchise_ids`` so ``first_team_id`` picks first; default is smallest id."""
    ids = list(franchise_ids)
    if not ids:
        return []
    if len(ids) == 1:
        return ids
    first = int(first_team_id) if first_team_id is not None else ids[0]
    if first not in ids:
        first = ids[0]
    i = ids.index(first)
    return ids[i:] + ids[:i]


def _pos_tokens_for_player(pl: Player, tok_cache: dict[int, set[str]] | None = None) -> set[str]:
    if tok_cache is not None:
        pid = int(pl.id)
        if pid in tok_cache:
            return tok_cache[pid]
    fid = getattr(pl, "fhm_player_id", None)
    rr = get_player_ratings_row(str(fid).strip() if fid else None)
    label = eligible_positions_from_ratings_row(rr, ELIGIBLE_POSITION_DISPLAY_MIN_RATING)
    if label:
        out = {tok.strip().upper() for tok in label.replace(",", "•").split("•") if tok.strip()}
    else:
        pos = (getattr(pl, "position", None) or "").strip().upper()
        out = {pos} if pos else set()
    if tok_cache is not None:
        tok_cache[int(pl.id)] = out
    return out


def player_is_goalie(pl: Player, tok_cache: dict[int, set[str]] | None = None) -> bool:
    pos = (getattr(pl, "position", None) or "").strip().upper()
    if pos == "G":
        return True
    tokens = _pos_tokens_for_player(pl, tok_cache)
    if not tokens:
        return False
    if "G" in tokens and not (tokens & (_FORWARD_TOKENS | _DEFENSE_TOKENS)):
        return True
    return False


def player_is_forward(pl: Player, tok_cache: dict[int, set[str]] | None = None) -> bool:
    if player_is_goalie(pl, tok_cache):
        return False
    tokens = _pos_tokens_for_player(pl, tok_cache)
    if tokens & _FORWARD_TOKENS:
        return True
    pos = (getattr(pl, "position", None) or "").strip().upper()
    return pos in _FORWARD_TOKENS


def player_is_defense(pl: Player, tok_cache: dict[int, set[str]] | None = None) -> bool:
    if player_is_goalie(pl, tok_cache):
        return False
    tokens = _pos_tokens_for_player(pl, tok_cache)
    if tokens & _DEFENSE_TOKENS:
        return True
    pos = (getattr(pl, "position", None) or "").strip().upper()
    return pos in _DEFENSE_TOKENS


def player_skater_category(pl: Player, tok_cache: dict[int, set[str]] | None = None) -> str | None:
    """Return 'forward' or 'defense' for skater phase; None if unclassified."""
    if player_is_goalie(pl, tok_cache):
        return None
    if player_is_forward(pl, tok_cache):
        return "forward"
    if player_is_defense(pl, tok_cache):
        return "defense"
    return None


def slots_ordered(session: Session, draft_id: int) -> list[LeagueExpansionDraftSlot]:
    return list(
        session.scalars(
            select(LeagueExpansionDraftSlot)
            .where(LeagueExpansionDraftSlot.league_expansion_draft_id == draft_id)
            .order_by(LeagueExpansionDraftSlot.overall_pick.asc())
        ).all()
    )


def picked_player_ids(session: Session, draft_id: int) -> set[int]:
    rows = session.scalars(
        select(LeagueExpansionDraftPick.player_id).where(
            LeagueExpansionDraftPick.league_expansion_draft_id == draft_id
        )
    ).all()
    return {int(x) for x in rows}


def losses_by_team_from_picks(session: Session, draft_id: int) -> dict[int, int]:
    out: dict[int, int] = {}
    for ft in session.scalars(
        select(LeagueExpansionDraftPick.from_team_id).where(
            LeagueExpansionDraftPick.league_expansion_draft_id == draft_id,
            LeagueExpansionDraftPick.from_team_id.isnot(None),
        )
    ).all():
        tid = int(ft)
        out[tid] = out.get(tid, 0) + 1
    return out


def featured_expansion_draft(session: Session, league_slug: str) -> LeagueExpansionDraft | None:
    live = session.scalar(
        select(LeagueExpansionDraft)
        .where(LeagueExpansionDraft.league_slug == league_slug, LeagueExpansionDraft.status == "live")
        .order_by(LeagueExpansionDraft.id.desc())
        .limit(1)
    )
    if live:
        return live
    setup = session.scalar(
        select(LeagueExpansionDraft)
        .where(LeagueExpansionDraft.league_slug == league_slug, LeagueExpansionDraft.status == "setup")
        .order_by(LeagueExpansionDraft.id.desc())
        .limit(1)
    )
    if setup:
        return setup
    return session.scalar(
        select(LeagueExpansionDraft)
        .where(LeagueExpansionDraft.league_slug == league_slug, LeagueExpansionDraft.status == "completed")
        .order_by(LeagueExpansionDraft.id.desc())
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


def regenerate_slots(session: Session, draft: LeagueExpansionDraft) -> str | None:
    if draft.status != "setup":
        return "Can only regenerate slots while draft is in setup."
    franchises = expansion_franchise_ids_sorted(draft)
    if not franchises:
        return "Select at least one expansion franchise before generating slots."
    gr = max(0, int(draft.goalie_rounds))
    sr = max(0, int(draft.skater_rounds))
    if gr == 0 and sr == 0:
        return "Set at least one goalie round or skater round."
    g_first = getattr(draft, "goalie_phase_first_team_id", None)
    s_first = getattr(draft, "skater_phase_first_team_id", None)
    order_goalie = phase_pick_order(franchises, g_first)
    order_skater = phase_pick_order(franchises, s_first)
    session.execute(
        delete(LeagueExpansionDraftSlot).where(
            LeagueExpansionDraftSlot.league_expansion_draft_id == draft.id
        )
    )
    overall = 0
    for r in range(1, gr + 1):
        for tid in order_goalie:
            overall += 1
            session.add(
                LeagueExpansionDraftSlot(
                    league_expansion_draft_id=draft.id,
                    overall_pick=overall,
                    round=r,
                    phase="goalie",
                    team_id=int(tid),
                )
            )
    for r in range(1, sr + 1):
        for tid in order_skater:
            overall += 1
            session.add(
                LeagueExpansionDraftSlot(
                    league_expansion_draft_id=draft.id,
                    overall_pick=overall,
                    round=r,
                    phase="skater",
                    team_id=int(tid),
                )
            )
    draft.current_slot_index = 0
    invalidate_expansion_eligible_cache(draft.id)
    return None
    return session.scalar(
        select(LeagueExpansionDraftPick).where(
            LeagueExpansionDraftPick.league_expansion_draft_id == draft_id,
            LeagueExpansionDraftPick.overall_pick == overall,
        )
    )


def _finalize_if_done(session: Session, draft: LeagueExpansionDraft, slots: list[LeagueExpansionDraftSlot]) -> None:
    needed = [s for s in slots if not s.forfeited]
    picks = list(
        session.scalars(
            select(LeagueExpansionDraftPick).where(
                LeagueExpansionDraftPick.league_expansion_draft_id == draft.id
            )
        ).all()
    )
    if len(picks) < len(needed):
        return
    draft.status = "completed"
    draft.pick_started_at = None
    draft.pick_deadline_at = None
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    draft.awaiting_admin_resolution = False
    draft.completed_summary_json = json.dumps(_simple_completion_summary(session, draft))


def _simple_completion_summary(session: Session, draft: LeagueExpansionDraft) -> dict[str, Any]:
    picks = list(
        session.scalars(
            select(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
            .order_by(LeagueExpansionDraftPick.overall_pick.asc())
        ).all()
    )
    return {
        "draft_id": draft.id,
        "picks": len(picks),
        "note": "Expansion draft complete.",
    }


def sync_current_slot_and_clock(session: Session, draft: LeagueExpansionDraft) -> None:
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
        if draft.pick_deadline_at is None or draft.awaiting_admin_resolution:
            draft.pick_started_at = now
            draft.pick_deadline_at = now + timedelta(seconds=int(draft.timer_seconds))
            draft.deadline_extended_for_slot = False
        draft.awaiting_admin_resolution = False
        return

    _finalize_if_done(session, draft, slots)


def go_live(session: Session, draft: LeagueExpansionDraft, admin_user_id: int) -> str | None:
    del admin_user_id
    if draft.status != "setup":
        return "Draft is not in setup status."
    other = session.scalar(
        select(LeagueExpansionDraft)
        .where(
            LeagueExpansionDraft.league_slug == draft.league_slug,
            LeagueExpansionDraft.status == "live",
            LeagueExpansionDraft.id != draft.id,
        )
        .limit(1)
    )
    if other:
        return "Another expansion draft is already live for this site."
    slots = slots_ordered(session, draft.id)
    if not slots:
        return "Generate draft slots before going live."
    n_rows = int(
        session.scalar(
            select(func.count())
            .select_from(LeagueExpansionDraftEligiblePlayer)
            .where(LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == draft.id)
        )
        or 0
    )
    if n_rows < 1:
        return "Add eligible (unprotected) players before going live."

    players_for_board = eligible_players_for_board(session, draft)
    if not players_for_board:
        return (
            "No eligible players remain under current rules "
            "(check pool, age 21+, max losses per team, and phase limits)."
        )
    draft.board_ranks_json = json.dumps({str(p.id): i + 1 for i, p in enumerate(players_for_board)})
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


def pause_timer(session: Session, draft: LeagueExpansionDraft) -> str | None:
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


def resume_timer(session: Session, draft: LeagueExpansionDraft) -> str | None:
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


def expansion_process_tick(session: Session, draft: LeagueExpansionDraft) -> None:
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
        _finalize_if_done(session, draft, slots)
        return
    slot = slots[draft.current_slot_index]
    if slot.forfeited:
        sync_current_slot_and_clock(session, draft)
        return

    if not draft.deadline_extended_for_slot:
        base = draft.pick_deadline_at or utcnow_naive()
        draft.pick_deadline_at = base + timedelta(seconds=int(draft.empty_queue_timer_seconds))
        draft.deadline_extended_for_slot = True
        return

    draft.awaiting_admin_resolution = True
    draft.pick_deadline_at = None


def _team_at_max_losses(losses_by_team: dict[int, int], team_id: int | None, max_loss: int) -> bool:
    if team_id is None:
        return False
    return losses_by_team.get(int(team_id), 0) >= max(0, int(max_loss))


_EXP_ELIG_CACHE: dict[tuple[Any, ...], tuple[tuple[int, ...], float]] = {}
_EXP_ELIG_LOCK = Lock()
_EXP_ELIG_TTL_SEC = 18.0
_EXP_ELIG_MAX_KEYS = 48


def invalidate_expansion_eligible_cache(draft_id: int | None = None) -> None:
    """Drop cached ordered eligible id lists (after picks, pool edits, slot regen)."""
    with _EXP_ELIG_LOCK:
        if draft_id is None:
            _EXP_ELIG_CACHE.clear()
            return
        did = int(draft_id)
        stale = [k for k in _EXP_ELIG_CACHE if k[0] == did]
        for k in stale:
            del _EXP_ELIG_CACHE[k]


def _rights_holder_team_id_from_maps(
    pl: Player,
    prospect_by: dict[int, Prospect | None],
    team_by_id: dict[int, Team],
    team_by_fhm_id: dict[str, Team],
) -> int | None:
    org = organization_main_team_from_maps(
        pl,
        prospect_by_player_id=prospect_by,
        team_by_id=team_by_id,
        team_by_fhm_id=team_by_fhm_id,
    )
    if org is not None:
        return int(org.id)
    if pl.current_team_id is not None:
        return int(pl.current_team_id)
    return None


def _eligible_cache_bump_parts(session: Session, draft: LeagueExpansionDraft) -> tuple[int, float, int, int]:
    n_picks = int(
        session.scalar(
            select(func.count())
            .select_from(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
        )
        or 0
    )
    n_elig = int(
        session.scalar(
            select(func.count())
            .select_from(LeagueExpansionDraftEligiblePlayer)
            .where(LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == draft.id)
        )
        or 0
    )
    ts = draft.updated_at.timestamp() if draft.updated_at else 0.0
    return (int(draft.id), ts, n_picks, n_elig)


def _compute_eligible_player_ids_ordered(
    session: Session,
    draft: LeagueExpansionDraft,
    *,
    phase: str | None,
    expansion_team_id: int | None,
) -> list[int]:
    elig_ids = {
        int(x)
        for x in session.scalars(
            select(LeagueExpansionDraftEligiblePlayer.player_id).where(
                LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == draft.id
            )
        ).all()
    }
    if not elig_ids:
        return []
    picked = picked_player_ids(session, draft.id)
    losses = losses_by_team_from_picks(session, draft.id)
    max_loss = int(draft.max_players_lost_per_team)
    blocked_teams: set[int] = set()
    for tid, n in losses.items():
        if n >= max_loss:
            blocked_teams.add(int(tid))

    all_teams = list(session.scalars(select(Team)).all())
    team_by_id = {int(t.id): t for t in all_teams}
    team_by_fhm_id: dict[str, Team] = {}
    for t in all_teams:
        if t.fhm_team_id is not None:
            k = str(t.fhm_team_id).strip()
            if k:
                team_by_fhm_id[k] = t

    prospect_rows = session.scalars(select(Prospect).where(Prospect.player_id.in_(elig_ids))).all()
    prospect_by: dict[int, Prospect | None] = {int(pr.player_id): pr for pr in prospect_rows}

    players = list(
        session.scalars(
            select(Player)
            .where(Player.id.in_(elig_ids))
            .options(joinedload(Player.contract), joinedload(Player.current_team))
        )
        .unique()
        .all()
    )
    tok_cache: dict[int, set[str]] = {}
    candidates: list[Player] = []
    ph = (phase or "").strip().lower() if phase else ""
    for pl in players:
        if int(pl.id) in picked:
            continue
        loss_tid = _rights_holder_team_id_from_maps(pl, prospect_by, team_by_id, team_by_fhm_id)
        if loss_tid is not None and int(loss_tid) in blocked_teams:
            continue
        if ph == "goalie":
            if not player_is_goalie(pl, tok_cache):
                continue
        elif ph == "skater":
            if player_is_goalie(pl, tok_cache):
                continue
            if player_skater_category(pl, tok_cache) is None:
                continue
        if not _expansion_board_age_ok(pl):
            continue
        candidates.append(pl)

    def sort_key(p: Player) -> tuple:
        br = 10**9
        if draft.board_ranks_json:
            try:
                m = json.loads(draft.board_ranks_json)
                br = int(m.get(str(int(p.id)), 10**9))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        pot = p.overall_potential if p.overall_potential is not None else -1.0
        return (br, -float(pot), int(p.id))

    candidates.sort(key=sort_key)
    return [int(p.id) for p in candidates]


def hydrate_players_for_ordered_ids(session: Session, ordered_ids: list[int]) -> list[Player]:
    """Load :class:`Player` rows for ids (with contract + current team) preserving order."""
    if not ordered_ids:
        return []
    uniq = list(dict.fromkeys(int(i) for i in ordered_ids))
    rows = list(
        session.scalars(
            select(Player)
            .where(Player.id.in_(uniq))
            .options(joinedload(Player.contract), joinedload(Player.current_team))
        )
        .unique()
        .all()
    )
    by_id = {int(p.id): p for p in rows}
    return [by_id[i] for i in ordered_ids if i in by_id]


def eligible_player_ids_for_board(
    session: Session,
    draft: LeagueExpansionDraft,
    *,
    phase: str | None = None,
    expansion_team_id: int | None = None,
) -> list[int]:
    bump = _eligible_cache_bump_parts(session, draft)
    ph = (phase or "").strip().lower()
    ex = int(expansion_team_id) if expansion_team_id is not None else -1
    key = (*bump, ph, ex)
    now = monotonic()
    with _EXP_ELIG_LOCK:
        hit = _EXP_ELIG_CACHE.get(key)
        if hit is not None and (now - hit[1]) < _EXP_ELIG_TTL_SEC:
            return list(hit[0])
    ids = _compute_eligible_player_ids_ordered(
        session, draft, phase=phase, expansion_team_id=expansion_team_id
    )
    frozen = tuple(ids)
    store_at = monotonic()
    with _EXP_ELIG_LOCK:
        _EXP_ELIG_CACHE[key] = (frozen, store_at)
        while len(_EXP_ELIG_CACHE) > _EXP_ELIG_MAX_KEYS:
            drop = min(_EXP_ELIG_CACHE, key=lambda kk: _EXP_ELIG_CACHE[kk][1])
            del _EXP_ELIG_CACHE[drop]
    return list(frozen)


def eligible_players_for_board(
    session: Session,
    draft: LeagueExpansionDraft,
    *,
    phase: str | None = None,
    expansion_team_id: int | None = None,
) -> list[Player]:
    """Ordered list of players on the board for UI / BPA (excludes picked, max-loss teams, phase, caps).

    Always omits players under 21 (same league age reference as the rest of the site), even if
    they were saved in the commissioner eligible pool. Uses batched SQL + a short TTL cache.
    """
    ids = eligible_player_ids_for_board(
        session, draft, phase=phase, expansion_team_id=expansion_team_id
    )
    return hydrate_players_for_ordered_ids(session, ids)


def validate_pick(
    session: Session,
    draft: LeagueExpansionDraft,
    slot: LeagueExpansionDraftSlot,
    pl: Player,
) -> str | None:
    elig = session.scalar(
        select(LeagueExpansionDraftEligiblePlayer).where(
            LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == draft.id,
            LeagueExpansionDraftEligiblePlayer.player_id == int(pl.id),
        )
    )
    if not elig:
        return "Player is not in the eligible pool."
    if not _expansion_board_age_ok(pl):
        return "Player is under 21 (league age) and cannot be selected in the expansion draft."
    if int(pl.id) in picked_player_ids(session, draft.id):
        return "Player was already drafted."
    losses = losses_by_team_from_picks(session, draft.id)
    max_loss = int(draft.max_players_lost_per_team)
    loss_tid = _rights_holder_team_id_for_losses(session, pl)
    if loss_tid is not None:
        tid = int(loss_tid)
        if losses.get(tid, 0) >= max_loss:
            return "This team has already lost the maximum number of players."
        # taking this pick would be losses[tid]+1 — allowed if < max_loss before pick; we're at < max here
    ph = (slot.phase or "").strip().lower()
    if ph == "goalie":
        if not player_is_goalie(pl):
            return "This pick must be a goalie."
    elif ph == "skater":
        if player_is_goalie(pl):
            return "This pick must be a skater (forward or defense)."
        if player_skater_category(pl) is None:
            return "Player position is not valid for the skater round."
    return None


def record_pick(
    session: Session,
    draft: LeagueExpansionDraft,
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
    pl = session.get(Player, int(player_id))
    if not pl:
        return "Player not found."
    err = validate_pick(session, draft, slot, pl)
    if err:
        return err
    if source == "gm":
        if user_id is None:
            return "Not authenticated."
        mids = gm_user_ids_for_team(session, draft.league_slug, slot.team_id)
        if int(user_id) not in mids:
            return "You are not the GM on the clock for this team."
        ddl = draft.pick_deadline_at
        if ddl is not None and utcnow_naive() > ddl:
            return "Pick timer has expired; ask the commissioner."
        order = set(expansion_franchise_ids_sorted(draft))
        if order and int(slot.team_id) not in order:
            return "This team is not an expansion franchise in this draft."

    from_tid = _rights_holder_team_id_for_losses(session, pl)
    pk = LeagueExpansionDraftPick(
        league_expansion_draft_id=draft.id,
        overall_pick=int(slot.overall_pick),
        round=int(slot.round),
        phase=str(slot.phase),
        team_id=int(slot.team_id),
        player_id=int(pl.id),
        from_team_id=int(from_tid) if from_tid is not None else None,
        source=source,
        picked_by_user_id=int(user_id) if user_id is not None else None,
    )
    session.add(pk)
    draft.current_slot_index += 1
    draft.deadline_extended_for_slot = False
    draft.awaiting_admin_resolution = False
    draft.timer_paused = False
    draft.timer_paused_remaining_seconds = None
    sync_current_slot_and_clock(session, draft)
    slots2 = slots_ordered(session, draft.id)
    _finalize_if_done(session, draft, slots2)
    invalidate_expansion_eligible_cache(draft.id)
    return None


def resolve_admin_pick(
    session: Session,
    draft: LeagueExpansionDraft,
    player_id: int,
    admin_user_id: int,
) -> str | None:
    if draft.status != "live":
        return "Draft is not live."
    return record_pick(session, draft, player_id, admin_user_id, "admin")


def undo_last_pick(session: Session, draft: LeagueExpansionDraft) -> str | None:
    if draft.status != "live":
        return "Can only undo during a live draft."
    picks = list(
        session.scalars(
            select(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
            .order_by(LeagueExpansionDraftPick.overall_pick.desc())
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
    invalidate_expansion_eligible_cache(draft.id)
    return None
    session.execute(
        delete(LeagueExpansionDraftEligiblePlayer).where(
            LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == draft.id
        )
    )
    for pid in sorted(player_ids):
        session.add(
            LeagueExpansionDraftEligiblePlayer(
                league_expansion_draft_id=draft.id,
                player_id=int(pid),
            )
        )
    invalidate_expansion_eligible_cache(draft.id)
