"""NHL-style 4-ball draft lottery for BOWL-Relegation Draft Hub.

Combination universe is C(14,4) = 1001. One combo is unused (redraw). The other
1000 are allocated along the current NHL 16-team weight curve, scaled when the
field is not 16 teams.

``lottery_seed_teams`` is the only place to later switch from the admin ranking
to combined Upper/Lower standings (drop the two best records, then take 16).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.site_models import LeagueDraft, LeagueDraftLottery, LeagueDraftPick, LeagueDraftSlot

RELEGATION_LOTTERY_SLUG = "bowl-fantasy"
BALL_COUNT = 14
COMBO_SIZE = 4
ASSIGNED_COMBO_COUNT = 1000
DEFAULT_LOTTERY_TEAM_COUNT = 16
DEFAULT_DRAW_COUNT = 2
# Published NHL pick-1 shape (25.5% down to 0.5%), adjusted so the 16 counts
# sum to 1000 assigned combinations. Seed 1 stays 255; 15 and 16 share the floor.
NHL_16_WEIGHTS: tuple[int, ...] = (
    255,
    130,
    110,
    90,
    80,
    70,
    60,
    50,
    40,
    30,
    25,
    20,
    15,
    10,
    10,
    5,
)

STATUS_PENDING = "pending"
STATUS_LOCKED_1 = "locked_1"
STATUS_COMPLETE = "complete"


@dataclass(frozen=True)
class LotterySeed:
    """One lottery participant (original franchise + current 1st-round owner)."""

    seed: int
    original_team_id: int
    owner_team_id: int
    combo_count: int


def is_relegation_lottery_league(league_slug: str | None) -> bool:
    return str(league_slug or "").strip() == RELEGATION_LOTTERY_SLUG


def all_combinations() -> list[tuple[int, ...]]:
    """Lexicographic 4-ball combos from balls 1..14."""
    return list(combinations(range(1, BALL_COUNT + 1), COMBO_SIZE))


def combo_key(combo: Iterable[int]) -> str:
    balls = sorted(int(x) for x in combo)
    return "-".join(str(b) for b in balls)


def parse_combo(raw: str | Iterable[int]) -> tuple[int, ...]:
    if isinstance(raw, str):
        parts = [int(p) for p in raw.replace("—", "-").replace(",", "-").split("-") if p.strip().isdigit()]
        return tuple(sorted(parts))
    return tuple(sorted(int(x) for x in raw))


def scale_combo_counts(team_count: int) -> list[int]:
    """Distribute 1000 combos across *team_count* seeds using the NHL curve."""
    n = max(1, int(team_count))
    if n == len(NHL_16_WEIGHTS):
        return list(NHL_16_WEIGHTS)
    weights: list[float]
    if n < len(NHL_16_WEIGHTS):
        weights = [float(w) for w in NHL_16_WEIGHTS[:n]]
    else:
        weights = [float(w) for w in NHL_16_WEIGHTS]
        tail = float(NHL_16_WEIGHTS[-1])
        for _ in range(n - len(NHL_16_WEIGHTS)):
            tail = max(1.0, tail * 0.6)
            weights.append(tail)
    total_w = sum(weights)
    raw = [w * ASSIGNED_COMBO_COUNT / total_w for w in weights]
    counts = [int(x) for x in raw]
    leftover = ASSIGNED_COMBO_COUNT - sum(counts)
    frac_order = sorted(range(n), key=lambda i: (raw[i] - counts[i], -i), reverse=True)
    for i in range(leftover):
        counts[frac_order[i % n]] += 1
    while min(counts) < 1 and max(counts) > 1:
        donor = counts.index(max(counts))
        recv = counts.index(min(counts))
        counts[donor] -= 1
        counts[recv] += 1
    return counts


def allocate_combinations(
    team_count: int, rng: random.Random | None = None
) -> tuple[str, dict[str, int], list[int]]:
    """Assign 1000 combos to seeds 1..N. Returns (unused_key, combo_to_seed, counts)."""
    n = max(1, int(team_count))
    counts = scale_combo_counts(n)
    universe = all_combinations()
    unused = combo_key(universe[-1])
    assigned = [combo_key(c) for c in universe[:-1]]
    mixer = rng or random.Random()
    mixer.shuffle(assigned)
    combo_to_seed: dict[str, int] = {}
    cursor = 0
    for seed, count in enumerate(counts, start=1):
        chunk = assigned[cursor : cursor + count]
        cursor += count
        for key in chunk:
            combo_to_seed[key] = seed
    return unused, combo_to_seed, counts


def odds_matrix(counts: list[int], draw_count: int = DEFAULT_DRAW_COUNT) -> list[dict[str, Any]]:
    """P(seed i lands pick k) and average slot after *draw_count* NHL-style draws."""
    n = len(counts)
    draws = max(1, min(int(draw_count), n))
    total = float(sum(counts)) or 1.0
    pick_probs = [[0.0] * n for _ in range(n)]

    def rec(drawn: list[int], p: float, rem: float) -> None:
        if len(drawn) >= draws or rem <= 0:
            winners = set(drawn)
            for i in range(n):
                if i in winners:
                    pick = drawn.index(i)
                else:
                    worse_winners = sum(1 for w in drawn if w < i)
                    pick = i + draws - worse_winners
                if 0 <= pick < n:
                    pick_probs[i][pick] += p
            return
        drawn_set = set(drawn)
        for j in range(n):
            if j in drawn_set or counts[j] <= 0:
                continue
            rec(drawn + [j], p * (counts[j] / rem), rem - counts[j])

    rec([], 1.0, total)
    rows: list[dict[str, Any]] = []
    for i, count in enumerate(counts):
        pcts = [round(prob * 100.0, 1) for prob in pick_probs[i]]
        avg = sum((k + 1) * pick_probs[i][k] for k in range(n))
        rows.append(
            {
                "seed": i + 1,
                "combo_count": int(count),
                "pick1_pct": pcts[0] if pcts else 0.0,
                "pick_pcts": pcts,
                "avg": round(avg, 1),
            }
        )
    return rows


def lottery_seed_teams(
    ranking_original_team_ids: list[int],
    *,
    team_count: int = DEFAULT_LOTTERY_TEAM_COUNT,
) -> list[int]:
    """Return original-franchise ids that enter the lottery.

    Today this is the worst *team_count* clubs from the admin ranking.
    Later: combined Upper+Lower standings, drop the two best records, then take 16.
    """
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ranking_original_team_ids:
        tid = int(raw)
        if tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    n = max(0, int(team_count))
    return ordered[:n]


def draw_combination(
    combo_to_seed: dict[str, int],
    unused_key: str,
    excluded_seeds: set[int],
    rng: random.Random | None = None,
) -> tuple[tuple[int, ...], int]:
    """Draw a 4-ball combo, redrawing unused or already-won seeds."""
    mixer = rng or random.Random()
    live_keys = [key for key, seed in combo_to_seed.items() if seed not in excluded_seeds]
    if not live_keys:
        raise ValueError("No combinations remain for this draw.")
    pool = live_keys + [unused_key]
    for _ in range(64):
        key = mixer.choice(pool)
        if key == unused_key or key not in combo_to_seed:
            continue
        seed = int(combo_to_seed[key])
        if seed in excluded_seeds:
            continue
        return parse_combo(key), seed
    # Exhaustive fallback if unused is over-represented in a tiny pool.
    key = mixer.choice(live_keys)
    return parse_combo(key), int(combo_to_seed[key])


def apply_lottery_round1_order(
    seeds: list[LotterySeed],
    winner_seeds: list[int],
    tail: list[LotterySeed],
) -> list[LotterySeed]:
    """Winners take the top draws; leftover lottery seeds keep original order; tail stays."""
    won = [int(s) for s in winner_seeds]
    won_set = set(won)
    by_seed = {int(s.seed): s for s in seeds}
    head = [by_seed[s] for s in won if s in by_seed]
    rest = [s for s in seeds if int(s.seed) not in won_set]
    return [*head, *rest, *tail]


def _json_load(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def get_lottery(session: Session, draft_id: int) -> LeagueDraftLottery | None:
    return session.scalar(
        select(LeagueDraftLottery).where(LeagueDraftLottery.league_draft_id == int(draft_id)).limit(1)
    )


def draft_has_picks(session: Session, draft_id: int) -> bool:
    n = session.scalar(
        select(func.count()).select_from(LeagueDraftPick).where(
            LeagueDraftPick.league_draft_id == int(draft_id)
        )
    )
    return int(n or 0) > 0


def lottery_is_complete(lottery: LeagueDraftLottery | None) -> bool:
    return bool(lottery) and str(lottery.status or "") == STATUS_COMPLETE


def gm_picks_blocked_by_lottery(
    session: Session, draft: LeagueDraft, *, league_slug: str | None = None
) -> bool:
    slug = str(league_slug or getattr(draft, "league_slug", "") or "")
    if not is_relegation_lottery_league(slug):
        return False
    row = get_lottery(session, int(draft.id))
    return not lottery_is_complete(row)


def delete_lottery_for_draft(session: Session, draft_id: int) -> None:
    row = get_lottery(session, draft_id)
    if row is not None:
        session.delete(row)


def round1_slots(session: Session, draft_id: int) -> list[LeagueDraftSlot]:
    return list(
        session.scalars(
            select(LeagueDraftSlot)
            .where(LeagueDraftSlot.league_draft_id == int(draft_id), LeagueDraftSlot.round == 1)
            .order_by(LeagueDraftSlot.overall_pick.asc())
        ).all()
    )


def _round1_slots(session: Session, draft_id: int) -> list[LeagueDraftSlot]:
    return round1_slots(session, draft_id)


def _seeds_from_round1(
    slots: list[LeagueDraftSlot], team_count: int
) -> tuple[list[LotterySeed], list[LotterySeed]]:
    lottery_slots = slots[: max(0, int(team_count))]
    tail_slots = slots[max(0, int(team_count)) :]
    seeds: list[LotterySeed] = []
    for idx, slot in enumerate(lottery_slots, start=1):
        original = int(slot.original_team_id or slot.team_id)
        owner = int(slot.team_id)
        seeds.append(
            LotterySeed(
                seed=idx,
                original_team_id=original,
                owner_team_id=owner,
                combo_count=0,
            )
        )
    tail = [
        LotterySeed(
            seed=len(seeds) + idx,
            original_team_id=int(slot.original_team_id or slot.team_id),
            owner_team_id=int(slot.team_id),
            combo_count=0,
        )
        for idx, slot in enumerate(tail_slots, start=1)
    ]
    return seeds, tail


def _snapshot_round1(slots: list[LeagueDraftSlot]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slot in slots:
        out.append(
            {
                "overall": int(slot.overall_pick),
                "original_team_id": int(slot.original_team_id or slot.team_id),
                "team_id": int(slot.team_id),
                "boost_tier": str(slot.boost_tier or ""),
                "penalty_pick": bool(getattr(slot, "penalty_pick", False)),
            }
        )
    return out


def arm_lottery(
    session: Session,
    draft: LeagueDraft,
    *,
    team_count: int = DEFAULT_LOTTERY_TEAM_COUNT,
    draw_count: int = DEFAULT_DRAW_COUNT,
    rng: random.Random | None = None,
) -> tuple[LeagueDraftLottery | None, str | None]:
    """Freeze seeds/combos from current round-1 slots."""
    if str(draft.status or "") not in ("setup", "live"):
        return None, "Lottery can only be armed while the draft is in setup or live."
    if draft_has_picks(session, int(draft.id)):
        return None, "Lottery cannot be armed after a pick has been made."
    slots = _round1_slots(session, int(draft.id))
    if len(slots) < 2:
        return None, "Add a first-round draft order before arming the lottery."
    n = max(2, min(int(team_count or DEFAULT_LOTTERY_TEAM_COUNT), len(slots)))
    draws = max(1, min(int(draw_count or DEFAULT_DRAW_COUNT), n))
    seeds, tail = _seeds_from_round1(slots, n)
    unused, combo_to_seed, counts = allocate_combinations(len(seeds), rng=rng)
    seed_rows = []
    for seed, count in zip(seeds, counts, strict=True):
        seed_rows.append(
            {
                "seed": seed.seed,
                "original_team_id": seed.original_team_id,
                "owner_team_id": seed.owner_team_id,
                "combo_count": int(count),
            }
        )
    existing = get_lottery(session, int(draft.id))
    if existing is None:
        existing = LeagueDraftLottery(league_draft_id=int(draft.id))
        session.add(existing)
    existing.team_count = n
    existing.draw_count = draws
    existing.status = STATUS_PENDING
    existing.seeds_json = json.dumps(seed_rows)
    existing.combo_map_json = json.dumps(combo_to_seed)
    existing.unused_combo = unused
    existing.draws_json = "[]"
    existing.round1_snapshot_json = json.dumps(_snapshot_round1(slots))
    existing.tail_json = json.dumps(
        [
            {
                "original_team_id": t.original_team_id,
                "owner_team_id": t.owner_team_id,
            }
            for t in tail
        ]
    )
    existing.updated_at = datetime.utcnow()
    if existing.created_at is None:
        existing.created_at = existing.updated_at
    return existing, None


def _seed_rows(lottery: LeagueDraftLottery) -> list[dict[str, Any]]:
    rows = _json_load(lottery.seeds_json, [])
    return rows if isinstance(rows, list) else []


def _draw_rows(lottery: LeagueDraftLottery) -> list[dict[str, Any]]:
    rows = _json_load(lottery.draws_json, [])
    return rows if isinstance(rows, list) else []


def _combo_map(lottery: LeagueDraftLottery) -> dict[str, int]:
    raw = _json_load(lottery.combo_map_json, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, seed in raw.items():
        try:
            out[str(key)] = int(seed)
        except (TypeError, ValueError):
            continue
    return out


def execute_draw(
    session: Session,
    draft: LeagueDraft,
    *,
    rng: random.Random | None = None,
) -> tuple[LeagueDraftLottery | None, dict[str, Any] | None, str | None]:
    """Official next draw. After the last draw, rewrite round-1 slots."""
    lottery = get_lottery(session, int(draft.id))
    if lottery is None:
        return None, None, "Arm the lottery before drawing."
    if str(draft.status or "") not in ("setup", "live"):
        return lottery, None, "Lottery draws are only allowed in setup or live."
    if draft_has_picks(session, int(draft.id)):
        return lottery, None, "Lottery cannot run after a pick has been made."
    if lottery_is_complete(lottery):
        return lottery, None, "Lottery is already complete."
    draws = _draw_rows(lottery)
    next_pick = len(draws) + 1
    if next_pick > int(lottery.draw_count or DEFAULT_DRAW_COUNT):
        return lottery, None, "All lottery draws are already complete."
    excluded = {int(row.get("seed") or 0) for row in draws if row.get("seed")}
    combo, seed = draw_combination(
        _combo_map(lottery),
        str(lottery.unused_combo or ""),
        excluded,
        rng=rng,
    )
    seed_row = next((r for r in _seed_rows(lottery) if int(r.get("seed") or 0) == seed), None)
    result = {
        "pick": next_pick,
        "combo": list(combo),
        "combo_sorted": list(combo),
        "seed": seed,
        "original_team_id": int(seed_row["original_team_id"]) if seed_row else None,
        "owner_team_id": int(seed_row["owner_team_id"]) if seed_row else None,
        "combo_count": int(seed_row["combo_count"]) if seed_row else 0,
        "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    draws.append(result)
    lottery.draws_json = json.dumps(draws)
    lottery.updated_at = datetime.utcnow()
    if next_pick >= int(lottery.draw_count or DEFAULT_DRAW_COUNT):
        err = apply_lottery_to_slots(session, draft, lottery)
        if err:
            return lottery, None, err
        lottery.status = STATUS_COMPLETE
    else:
        lottery.status = STATUS_LOCKED_1
    return lottery, result, None


def apply_lottery_to_slots(
    session: Session, draft: LeagueDraft, lottery: LeagueDraftLottery
) -> str | None:
    """Rewrite round-1 owners after both draws. Later rounds are untouched."""
    if draft_has_picks(session, int(draft.id)):
        return "Lottery results cannot change slots after a pick has been made."
    slots = _round1_slots(session, int(draft.id))
    if not slots:
        return "No first-round slots to update."
    seed_rows = _seed_rows(lottery)
    seeds = [
        LotterySeed(
            seed=int(row.get("seed") or 0),
            original_team_id=int(row.get("original_team_id") or 0),
            owner_team_id=int(row.get("owner_team_id") or 0),
            combo_count=int(row.get("combo_count") or 0),
        )
        for row in seed_rows
        if row.get("seed")
    ]
    tail_rows = _json_load(lottery.tail_json, [])
    tail = [
        LotterySeed(
            seed=len(seeds) + idx,
            original_team_id=int(row.get("original_team_id") or 0),
            owner_team_id=int(row.get("owner_team_id") or 0),
            combo_count=0,
        )
        for idx, row in enumerate(tail_rows if isinstance(tail_rows, list) else [], start=1)
    ]
    winners = [int(row.get("seed") or 0) for row in _draw_rows(lottery) if row.get("seed")]
    ordered = apply_lottery_round1_order(seeds, winners, tail)
    if len(ordered) != len(slots):
        return "Lottery field no longer matches the first-round slot count."
    for slot, seed in zip(slots, ordered, strict=True):
        slot.original_team_id = int(seed.original_team_id)
        slot.team_id = int(seed.owner_team_id)
    return None


def reset_lottery(
    session: Session, draft: LeagueDraft, *, rng: random.Random | None = None
) -> tuple[LeagueDraftLottery | None, str | None]:
    """Restore pre-lottery round-1 slots and re-arm. Blocked after picks."""
    lottery = get_lottery(session, int(draft.id))
    if lottery is None:
        return None, "No lottery to reset."
    if draft_has_picks(session, int(draft.id)):
        return lottery, "Lottery cannot be reset after a pick has been made."
    snapshot = _json_load(lottery.round1_snapshot_json, [])
    if isinstance(snapshot, list) and snapshot:
        by_overall = {int(row.get("overall") or 0): row for row in snapshot if row.get("overall")}
        for slot in _round1_slots(session, int(draft.id)):
            row = by_overall.get(int(slot.overall_pick))
            if not row:
                continue
            slot.original_team_id = int(row.get("original_team_id") or slot.original_team_id or slot.team_id)
            slot.team_id = int(row.get("team_id") or slot.team_id)
    return arm_lottery(
        session,
        draft,
        team_count=int(lottery.team_count or DEFAULT_LOTTERY_TEAM_COUNT),
        draw_count=int(lottery.draw_count or DEFAULT_DRAW_COUNT),
        rng=rng,
    )


def projected_round1_order(lottery: LeagueDraftLottery) -> list[dict[str, Any]]:
    """Current first-round projection from seeds + completed draws."""
    seed_rows = _seed_rows(lottery)
    seeds = [
        LotterySeed(
            seed=int(row.get("seed") or 0),
            original_team_id=int(row.get("original_team_id") or 0),
            owner_team_id=int(row.get("owner_team_id") or 0),
            combo_count=int(row.get("combo_count") or 0),
        )
        for row in seed_rows
        if row.get("seed")
    ]
    tail_rows = _json_load(lottery.tail_json, [])
    tail = [
        LotterySeed(
            seed=len(seeds) + idx,
            original_team_id=int(row.get("original_team_id") or 0),
            owner_team_id=int(row.get("owner_team_id") or 0),
            combo_count=0,
        )
        for idx, row in enumerate(tail_rows if isinstance(tail_rows, list) else [], start=1)
    ]
    winners = [int(row.get("seed") or 0) for row in _draw_rows(lottery) if row.get("seed")]
    if not winners:
        ordered = [*seeds, *tail]
    else:
        ordered = apply_lottery_round1_order(seeds, winners, tail)
    out: list[dict[str, Any]] = []
    for idx, seed in enumerate(ordered, start=1):
        out.append(
            {
                "overall": idx,
                "seed": seed.seed,
                "original_team_id": seed.original_team_id,
                "owner_team_id": seed.owner_team_id,
                "lottery": seed.seed <= len(seeds),
            }
        )
    return out


def lottery_public_payload(
    lottery: LeagueDraftLottery | None,
    *,
    team_meta: dict[int, dict[str, Any]],
    can_admin: bool,
    enabled: bool,
    unarmed_seeds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """JSON for the Draft Hub panel and /api/lottery."""
    empty = {
        "enabled": bool(enabled),
        "armed": False,
        "status": "unarmed",
        "team_count": DEFAULT_LOTTERY_TEAM_COUNT,
        "draw_count": DEFAULT_DRAW_COUNT,
        "can_admin": bool(can_admin),
        "seeds": unarmed_seeds or [],
        "draws": [],
        "odds": [],
        "combo_to_seed": {},
        "unused_combo": "",
        "round1_order": [],
        "complete": False,
    }
    if not enabled:
        return {**empty, "enabled": False}
    if lottery is None:
        counts = scale_combo_counts(len(unarmed_seeds) if unarmed_seeds else DEFAULT_LOTTERY_TEAM_COUNT)
        if unarmed_seeds:
            empty["odds"] = odds_matrix(counts[: len(unarmed_seeds)])
            empty["team_count"] = len(unarmed_seeds)
        return empty

    seed_rows = _seed_rows(lottery)
    counts = [int(row.get("combo_count") or 0) for row in seed_rows]
    if not counts:
        counts = scale_combo_counts(int(lottery.team_count or DEFAULT_LOTTERY_TEAM_COUNT))
    odds = odds_matrix(counts, int(lottery.draw_count or DEFAULT_DRAW_COUNT))
    odds_by_seed = {int(row["seed"]): row for row in odds}

    def _team_blob(tid: int | None) -> dict[str, Any]:
        if not tid:
            return {"id": None, "name": "", "abbr": "", "logo_url": None}
        meta = team_meta.get(int(tid)) or {}
        return {
            "id": int(tid),
            "name": str(meta.get("name") or ""),
            "abbr": str(meta.get("abbr") or ""),
            "logo_url": meta.get("logo_url"),
        }

    seeds_out: list[dict[str, Any]] = []
    for row in seed_rows:
        seed_no = int(row.get("seed") or 0)
        original_id = int(row.get("original_team_id") or 0)
        owner_id = int(row.get("owner_team_id") or 0)
        odds_row = odds_by_seed.get(seed_no) or {}
        seeds_out.append(
            {
                "seed": seed_no,
                "original_team_id": original_id,
                "owner_team_id": owner_id,
                "combo_count": int(row.get("combo_count") or 0),
                "pick1_pct": odds_row.get("pick1_pct"),
                "pick_pcts": odds_row.get("pick_pcts") or [],
                "avg": odds_row.get("avg"),
                "owner": _team_blob(owner_id),
                "original": _team_blob(original_id),
                "traded": owner_id != original_id,
            }
        )
    draws_out: list[dict[str, Any]] = []
    for row in _draw_rows(lottery):
        seed_no = int(row.get("seed") or 0)
        owner_id = int(row.get("owner_team_id") or 0)
        original_id = int(row.get("original_team_id") or 0)
        odds_row = odds_by_seed.get(seed_no) or {}
        draws_out.append(
            {
                **row,
                "owner": _team_blob(owner_id),
                "original": _team_blob(original_id),
                "pick1_pct": odds_row.get("pick1_pct"),
            }
        )
    order_out = []
    for row in projected_round1_order(lottery):
        owner_id = int(row.get("owner_team_id") or 0)
        original_id = int(row.get("original_team_id") or 0)
        order_out.append(
            {
                **row,
                "owner": _team_blob(owner_id),
                "original": _team_blob(original_id),
                "traded": owner_id != original_id,
            }
        )
    return {
        "enabled": True,
        "armed": True,
        "status": str(lottery.status or STATUS_PENDING),
        "team_count": int(lottery.team_count or DEFAULT_LOTTERY_TEAM_COUNT),
        "draw_count": int(lottery.draw_count or DEFAULT_DRAW_COUNT),
        "can_admin": bool(can_admin),
        "seeds": seeds_out,
        "draws": draws_out,
        "odds": odds,
        "combo_to_seed": _combo_map(lottery),
        "unused_combo": str(lottery.unused_combo or ""),
        "round1_order": order_out,
        "complete": lottery_is_complete(lottery),
        "updated_at": lottery.updated_at.isoformat() if lottery.updated_at else None,
    }


def unarmed_seed_preview(
    slots: list[LeagueDraftSlot],
    *,
    team_count: int = DEFAULT_LOTTERY_TEAM_COUNT,
    team_meta: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    seeds, _tail = _seeds_from_round1(slots, team_count)
    counts = scale_combo_counts(len(seeds)) if seeds else []
    odds = odds_matrix(counts) if counts else []
    odds_by_seed = {int(row["seed"]): row for row in odds}
    out: list[dict[str, Any]] = []
    for seed, count in zip(seeds, counts, strict=False):
        odds_row = odds_by_seed.get(seed.seed) or {}
        owner_meta = team_meta.get(seed.owner_team_id) or {}
        orig_meta = team_meta.get(seed.original_team_id) or {}
        out.append(
            {
                "seed": seed.seed,
                "original_team_id": seed.original_team_id,
                "owner_team_id": seed.owner_team_id,
                "combo_count": int(count),
                "pick1_pct": odds_row.get("pick1_pct"),
                "pick_pcts": odds_row.get("pick_pcts") or [],
                "avg": odds_row.get("avg"),
                "owner": {
                    "id": seed.owner_team_id,
                    "name": str(owner_meta.get("name") or ""),
                    "abbr": str(owner_meta.get("abbr") or ""),
                    "logo_url": owner_meta.get("logo_url"),
                },
                "original": {
                    "id": seed.original_team_id,
                    "name": str(orig_meta.get("name") or ""),
                    "abbr": str(orig_meta.get("abbr") or ""),
                    "logo_url": orig_meta.get("logo_url"),
                },
                "traded": seed.owner_team_id != seed.original_team_id,
            }
        )
    return out


def build_preview_lottery_payload(
    team_rows: list[dict[str, Any]],
    *,
    team_count: int = DEFAULT_LOTTERY_TEAM_COUNT,
    draw_count: int = DEFAULT_DRAW_COUNT,
    picks_per_round: int = 24,
    rng: random.Random | None = None,
    can_admin: bool = False,
) -> dict[str, Any]:
    """Armed demo lottery for the public preview page (no DB writes)."""
    rows = [r for r in team_rows if r.get("id")]
    if len(rows) < team_count:
        for n in range(len(rows) + 1, team_count + 1):
            rows.append(
                {
                    "id": 9000 + n,
                    "name": f"Demo Club {n}",
                    "abbr": f"D{n:02d}",
                    "logo_url": None,
                }
            )
    lottery_teams = rows[:team_count]
    tail_teams = rows[team_count:picks_per_round]
    unused, combo_to_seed, counts = allocate_combinations(team_count, rng=rng)
    odds = odds_matrix(counts, draw_count)
    odds_by_seed = {int(row["seed"]): row for row in odds}

    def blob(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": str(row.get("name") or ""),
            "abbr": str(row.get("abbr") or ""),
            "logo_url": row.get("logo_url"),
        }

    seeds_out: list[dict[str, Any]] = []
    for idx, (row, count) in enumerate(zip(lottery_teams, counts, strict=False), start=1):
        original_id = int(row["id"])
        owner_id = original_id
        original_row = row
        owner_row = row
        if idx == 5 and len(rows) > team_count:
            owner_row = rows[team_count]
            owner_id = int(owner_row["id"])
        odds_row = odds_by_seed.get(idx) or {}
        seeds_out.append(
            {
                "seed": idx,
                "original_team_id": original_id,
                "owner_team_id": owner_id,
                "combo_count": int(count),
                "pick1_pct": odds_row.get("pick1_pct"),
                "pick_pcts": odds_row.get("pick_pcts") or [],
                "avg": odds_row.get("avg"),
                "owner": blob(owner_row),
                "original": blob(original_row),
                "traded": owner_id != original_id,
            }
        )

    tail_seeds = [
        LotterySeed(
            seed=team_count + idx,
            original_team_id=int(row["id"]),
            owner_team_id=int(row["id"]),
            combo_count=0,
        )
        for idx, row in enumerate(tail_teams, start=1)
    ]
    lottery_seeds = [
        LotterySeed(
            seed=int(s["seed"]),
            original_team_id=int(s["original_team_id"]),
            owner_team_id=int(s["owner_team_id"]),
            combo_count=int(s["combo_count"]),
        )
        for s in seeds_out
    ]
    order_rows = apply_lottery_round1_order(lottery_seeds, [], tail_seeds)
    row_by_id = {int(r["id"]): r for r in rows}
    round1_order: list[dict[str, Any]] = []
    for seed in order_rows:
        orig = row_by_id.get(seed.original_team_id) or {"id": seed.original_team_id}
        owner = row_by_id.get(seed.owner_team_id) or orig
        round1_order.append(
            {
                "overall": len(round1_order) + 1,
                "seed": seed.seed,
                "original_team_id": seed.original_team_id,
                "owner_team_id": seed.owner_team_id,
                "lottery": seed.seed <= team_count,
                "owner": blob(owner),
                "original": blob(orig),
                "traded": seed.owner_team_id != seed.original_team_id,
            }
        )

    return {
        "enabled": True,
        "armed": True,
        "preview": True,
        "status": STATUS_PENDING,
        "team_count": team_count,
        "draw_count": draw_count,
        "can_admin": bool(can_admin),
        "seeds": seeds_out,
        "draws": [],
        "odds": odds,
        "combo_to_seed": combo_to_seed,
        "unused_combo": unused,
        "round1_order": round1_order,
        "complete": False,
    }

