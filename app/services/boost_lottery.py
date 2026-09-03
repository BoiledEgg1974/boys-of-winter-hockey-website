"""Draft boost lottery — weighted pick-number pool and draw (matches boost_lottery.js)."""
from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.boost_scratch import (
    DEFAULT_BASELINE_GOLD,
    DEFAULT_BASELINE_SILVER,
    draw_totals,
    load_scratch_extras,
)

DEFAULT_TRIPLE_LO = 28
DEFAULT_TRIPLE_HI = 81
DEFAULT_SINGLE_LO = 82
DEFAULT_SINGLE_HI = 216

BOOST_LOTTERY_LEAGUES = frozenset({"bowl-fantasy", "bowl-cap", "bowl-historical"})


class RandomLike(Protocol):
    def random(self) -> float: ...


def boost_lottery_theme(league_slug: str) -> str:
    return "fantasy" if league_slug == "bowl-fantasy" else ("cap" if league_slug == "bowl-cap" else "historical")


def is_boost_lottery_league(league_slug: str) -> bool:
    return str(league_slug or "") in BOOST_LOTTERY_LEAGUES


def validate_ranges(
    triple_lo: int,
    triple_hi: int,
    single_lo: int,
    single_hi: int,
) -> str | None:
    if not (triple_hi > triple_lo):
        return "Rounds 2–3: end must be greater than start (half-open [start, end))."
    if not (single_hi > single_lo):
        return "Rounds 4–8: end must be greater than start."
    return None


def build_pool(
    triple_lo: int,
    triple_hi: int,
    single_lo: int,
    single_hi: int,
) -> list[int]:
    tickets: list[int] = []
    for n in range(int(triple_lo), int(triple_hi)):
        tickets.extend([n, n, n])
    for n in range(int(single_lo), int(single_hi)):
        tickets.append(n)
    return tickets


def execute_draw(
    pool: list[int],
    gold_n: int,
    silver_n: int,
    rng: RandomLike | None = None,
) -> tuple[list[int], list[int], list[int]] | str:
    """Return (gold_winners, silver_winners, remaining_pool) or an error string."""
    rng = rng or random.Random()
    g = max(0, int(gold_n))
    s = max(0, int(silver_n))
    need = g + s
    if need == 0:
        return "Set at least one gold or silver winner."
    if not pool:
        return "Generate the ticket pool first."

    uniq = {int(t) for t in pool}
    if need > len(uniq):
        return (
            f"Not enough unique numbers in the pool. Need {need} unique winners "
            f"but only {len(uniq)} distinct values exist."
        )

    working = list(pool)
    picked: list[int] = []
    picked_set: set[int] = set()
    while len(picked) < need:
        candidates = [i for i, t in enumerate(working) if t not in picked_set]
        if not candidates:
            return "Could not fill all winner slots — pool exhausted."
        pick_i = candidates[int(rng.random() * len(candidates))]
        ticket = working.pop(pick_i)
        picked_set.add(ticket)
        picked.append(ticket)

    gold_winners = picked[:g]
    silver_winners = picked[g : g + s]
    remaining = [t for t in working if t not in picked_set]
    return gold_winners, silver_winners, remaining


def scratch_draw_totals(session: Session, league_slug: str) -> tuple[int, int, dict[str, Any]]:
    extras = load_scratch_extras(session, league_slug)
    draw_gold, draw_silver = draw_totals(
        DEFAULT_BASELINE_GOLD,
        DEFAULT_BASELINE_SILVER,
        extras["extra_gold"],
        extras["extra_silver"],
    )
    return draw_gold, draw_silver, extras


def load_boost_pool_row(session: Session, draft_id: int):
    from app.site_models import LeagueDraftBoostPool

    return session.scalars(
        select(LeagueDraftBoostPool).where(LeagueDraftBoostPool.league_draft_id == int(draft_id))
    ).first()


def get_or_create_boost_pool(session: Session, draft_id: int):
    from app.site_models import LeagueDraftBoostPool

    row = load_boost_pool_row(session, draft_id)
    if row is None:
        row = LeagueDraftBoostPool(
            league_draft_id=int(draft_id),
            triple_lo=DEFAULT_TRIPLE_LO,
            triple_hi=DEFAULT_TRIPLE_HI,
            single_lo=DEFAULT_SINGLE_LO,
            single_hi=DEFAULT_SINGLE_HI,
            pool_tickets_json="[]",
            last_gold_json="[]",
            last_silver_json="[]",
        )
        session.add(row)
        session.flush()
    return row


def pool_summary(pool: list[int]) -> dict[str, int]:
    uniq = {int(t) for t in pool}
    return {"ticket_count": len(pool), "unique_count": len(uniq)}


def generate_pool_for_draft(
    session: Session,
    draft,
    *,
    triple_lo: int,
    triple_hi: int,
    single_lo: int,
    single_hi: int,
) -> tuple[Any, str | None]:
    if draft.status != "setup":
        return None, "Boost lottery pool can only be built while the draft is in setup."
    err = validate_ranges(triple_lo, triple_hi, single_lo, single_hi)
    if err:
        return None, err
    tickets = build_pool(triple_lo, triple_hi, single_lo, single_hi)
    row = get_or_create_boost_pool(session, int(draft.id))
    row.triple_lo = int(triple_lo)
    row.triple_hi = int(triple_hi)
    row.single_lo = int(single_lo)
    row.single_hi = int(single_hi)
    row.pool_tickets_json = json.dumps(tickets)
    row.updated_at = datetime.utcnow()
    return row, None


def reset_pool_for_draft(session: Session, draft) -> tuple[Any | None, str | None]:
    if draft.status != "setup":
        return None, "Boost lottery pool can only be reset while the draft is in setup."
    row = load_boost_pool_row(session, int(draft.id))
    if row is None:
        return None, None
    row.pool_tickets_json = "[]"
    row.last_gold_json = "[]"
    row.last_silver_json = "[]"
    row.updated_at = datetime.utcnow()
    return row, None


def apply_boost_draw(
    session: Session,
    draft,
    league_slug: str,
    gold_picks: list[int],
    silver_picks: list[int],
    user_id: int,
) -> tuple[dict[str, Any], str | None]:
    from app.site_models import AdminAuditLog, BoostLotteryTeamResult, LeagueDraftSlot

    if draft.status != "setup":
        return {}, "Boost lottery draws can only be applied while the draft is in setup."

    gold = sorted({int(n) for n in gold_picks})
    silver = sorted({int(n) for n in silver_picks})
    overlap = set(gold) & set(silver)
    if overlap:
        return {}, (
            "Pick(s) listed as both gold and silver: "
            + ", ".join(str(n) for n in sorted(overlap))
            + "."
        )

    slot_rows = list(
        session.scalars(
            select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == int(draft.id))
        ).all()
    )
    if not slot_rows:
        return {}, "Add draft order slots before executing the boost lottery draw."

    slot_by_overall = {int(s.overall_pick): s for s in slot_rows}
    unknown = sorted(n for n in (gold + silver) if n not in slot_by_overall)
    if unknown:
        return {}, "No matching slot for pick(s): " + ", ".join(str(n) for n in unknown) + "."

    team_gold: dict[int, int] = {}
    team_silver: dict[int, int] = {}
    applied_gold = 0
    applied_silver = 0

    for ov in gold:
        slot = slot_by_overall[ov]
        slot.boost_tier = "gold"
        tid = int(slot.team_id)
        team_gold[tid] = team_gold.get(tid, 0) + 1
        applied_gold += 1

    for ov in silver:
        slot = slot_by_overall[ov]
        slot.boost_tier = "silver"
        tid = int(slot.team_id)
        team_silver[tid] = team_silver.get(tid, 0) + 1
        applied_silver += 1

    now = datetime.utcnow()
    for tid, count in team_gold.items():
        row = session.scalars(
            select(BoostLotteryTeamResult).where(
                BoostLotteryTeamResult.league_slug == league_slug,
                BoostLotteryTeamResult.team_id == tid,
            )
        ).first()
        if row is None:
            row = BoostLotteryTeamResult(
                league_slug=league_slug,
                team_id=tid,
                gold_count=count,
                silver_count=0,
                updated_by_user_id=user_id,
                updated_at=now,
            )
            session.add(row)
        else:
            row.gold_count = int(row.gold_count or 0) + count
            row.updated_by_user_id = user_id
            row.updated_at = now

    for tid, count in team_silver.items():
        row = session.scalars(
            select(BoostLotteryTeamResult).where(
                BoostLotteryTeamResult.league_slug == league_slug,
                BoostLotteryTeamResult.team_id == tid,
            )
        ).first()
        if row is None:
            row = BoostLotteryTeamResult(
                league_slug=league_slug,
                team_id=tid,
                gold_count=0,
                silver_count=count,
                updated_by_user_id=user_id,
                updated_at=now,
            )
            session.add(row)
        else:
            row.silver_count = int(row.silver_count or 0) + count
            row.updated_by_user_id = user_id
            row.updated_at = now

    session.add(
        AdminAuditLog(
            admin_user_id=int(user_id),
            league_slug=league_slug,
            action="draft_hub_boost_lottery_draw",
            detail_json=json.dumps(
                {
                    "draft_id": int(draft.id),
                    "gold": gold,
                    "silver": silver,
                    "team_gold": {str(k): v for k, v in team_gold.items()},
                    "team_silver": {str(k): v for k, v in team_silver.items()},
                }
            ),
        )
    )

    return {
        "gold": gold,
        "silver": silver,
        "applied_gold": applied_gold,
        "applied_silver": applied_silver,
    }, None


def execute_draw_for_draft(
    session: Session,
    draft,
    league_slug: str,
    user_id: int,
    rng: RandomLike | None = None,
) -> tuple[dict[str, Any], str | None]:
    if draft.status != "setup":
        return {}, "Boost lottery draws can only run while the draft is in setup."

    row = load_boost_pool_row(session, int(draft.id))
    if row is None:
        return {}, "Generate the ticket pool first."

    try:
        pool = json.loads(row.pool_tickets_json or "[]")
        if not isinstance(pool, list):
            pool = []
        pool = [int(x) for x in pool]
    except (TypeError, ValueError, json.JSONDecodeError):
        pool = []

    draw_gold, draw_silver, _extras = scratch_draw_totals(session, league_slug)
    result = execute_draw(pool, draw_gold, draw_silver, rng=rng)
    if isinstance(result, str):
        return {}, result

    gold_winners, silver_winners, remaining = result
    apply_payload, apply_err = apply_boost_draw(
        session,
        draft,
        league_slug,
        gold_winners,
        silver_winners,
        user_id,
    )
    if apply_err:
        return {}, apply_err

    row.pool_tickets_json = json.dumps(remaining)
    row.last_gold_json = json.dumps(gold_winners)
    row.last_silver_json = json.dumps(silver_winners)
    row.updated_at = datetime.utcnow()

    summary = pool_summary(remaining)
    return {
        **apply_payload,
        "remaining_tickets": summary["ticket_count"],
        "remaining_unique": summary["unique_count"],
        "draw_gold": draw_gold,
        "draw_silver": draw_silver,
    }, None


def applied_boost_count(session: Session, draft_id: int) -> tuple[int, int]:
    from app.site_models import LeagueDraftSlot

    rows = session.scalars(
        select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == int(draft_id))
    ).all()
    gold = sum(1 for s in rows if s.boost_tier == "gold")
    silver = sum(1 for s in rows if s.boost_tier == "silver")
    return gold, silver


def go_live_precheck(session: Session, draft) -> tuple[bool, str | None]:
    from app.services.draft_hub_eligibility_cache import eligible_players_for_board
    from app.services.draft_hub_state import draft_eligibility_params, slots_ordered
    from app.site_models import LeagueDraft

    if draft.status != "setup":
        return False, "Draft is not in setup status."
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
        return False, "Another draft is already live for this site. Complete or pause it first."
    slots = slots_ordered(session, draft.id)
    if not slots:
        return False, "Add draft order slots before going live."
    params = draft_eligibility_params(draft)
    players = eligible_players_for_board(
        session, draft.league_slug, params, set(), site_session=session
    )
    if not players:
        return False, "No eligible players for this draft (check age rules and pool)."
    return True, None


def public_boost_lottery_payload_pending(
    session: Session,
    league_slug: str,
    *,
    can_admin: bool,
    draft_year: int | None = None,
) -> dict[str, Any]:
    """Boost lottery panel before a commissioner creates the LeagueDraft event."""
    if not is_boost_lottery_league(league_slug):
        return {"enabled": False}

    draw_gold, draw_silver, extras = scratch_draw_totals(session, league_slug)
    blocker = "Create a draft event in Admin → Draft Hub before generating the pool or going live."
    return {
        "enabled": True,
        "can_admin": bool(can_admin),
        "draft_status": "pending",
        "show_panel": True,
        "no_draft_event": True,
        "draft_year": int(draft_year) if draft_year is not None else None,
        "baseline_gold": DEFAULT_BASELINE_GOLD,
        "baseline_silver": DEFAULT_BASELINE_SILVER,
        "extra_gold": extras["extra_gold"],
        "extra_silver": extras["extra_silver"],
        "draw_gold": draw_gold,
        "draw_silver": draw_silver,
        "scratch_complete": extras["complete"],
        "params": {
            "triple_lo": DEFAULT_TRIPLE_LO,
            "triple_hi": DEFAULT_TRIPLE_HI,
            "single_lo": DEFAULT_SINGLE_LO,
            "single_hi": DEFAULT_SINGLE_HI,
        },
        "pool_ready": False,
        "pool_summary": {"ticket_count": 0, "unique_count": 0},
        "last_gold": [],
        "last_silver": [],
        "applied_gold": 0,
        "applied_silver": 0,
        "can_go_live": False,
        "go_live_blocker": blocker if can_admin else None,
    }


def public_boost_lottery_payload(
    session: Session,
    draft,
    league_slug: str,
    *,
    can_admin: bool,
) -> dict[str, Any]:
    if not is_boost_lottery_league(league_slug) or draft is None:
        return {"enabled": False}

    draw_gold, draw_silver, extras = scratch_draw_totals(session, league_slug)
    row = load_boost_pool_row(session, int(draft.id))
    pool: list[int] = []
    params = {
        "triple_lo": DEFAULT_TRIPLE_LO,
        "triple_hi": DEFAULT_TRIPLE_HI,
        "single_lo": DEFAULT_SINGLE_LO,
        "single_hi": DEFAULT_SINGLE_HI,
    }
    last_gold: list[int] = []
    last_silver: list[int] = []
    if row is not None:
        params = {
            "triple_lo": int(row.triple_lo),
            "triple_hi": int(row.triple_hi),
            "single_lo": int(row.single_lo),
            "single_hi": int(row.single_hi),
        }
        try:
            pool = json.loads(row.pool_tickets_json or "[]")
            if not isinstance(pool, list):
                pool = []
            pool = [int(x) for x in pool]
        except (TypeError, ValueError, json.JSONDecodeError):
            pool = []
        try:
            last_gold = json.loads(row.last_gold_json or "[]")
            if not isinstance(last_gold, list):
                last_gold = []
            last_gold = [int(x) for x in last_gold]
        except (TypeError, ValueError, json.JSONDecodeError):
            last_gold = []
        try:
            last_silver = json.loads(row.last_silver_json or "[]")
            if not isinstance(last_silver, list):
                last_silver = []
            last_silver = [int(x) for x in last_silver]
        except (TypeError, ValueError, json.JSONDecodeError):
            last_silver = []

    pool_ready = len(pool) > 0
    summary = pool_summary(pool) if pool_ready else {"ticket_count": 0, "unique_count": 0}
    applied_gold, applied_silver = applied_boost_count(session, int(draft.id))
    can_go_live, go_live_blocker = go_live_precheck(session, draft)

    return {
        "enabled": True,
        "can_admin": bool(can_admin),
        "draft_status": draft.status,
        "show_panel": draft.status in ("setup", "live"),
        "baseline_gold": DEFAULT_BASELINE_GOLD,
        "baseline_silver": DEFAULT_BASELINE_SILVER,
        "extra_gold": extras["extra_gold"],
        "extra_silver": extras["extra_silver"],
        "draw_gold": draw_gold,
        "draw_silver": draw_silver,
        "scratch_complete": extras["complete"],
        "params": params,
        "pool_ready": pool_ready,
        "pool_summary": summary,
        "last_gold": last_gold,
        "last_silver": last_silver,
        "applied_gold": applied_gold,
        "applied_silver": applied_silver,
        "can_go_live": bool(can_admin and can_go_live),
        "go_live_blocker": go_live_blocker if can_admin and not can_go_live else None,
    }
