"""Draft boost scratch-ticket extras (fhmscout odds, BOWL ticket look).

Client JS copies these constants and the roll rules. Live extras stack on the
established gold/silver baseline before the weighted pick-number draw.
"""
from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

GOLD_P = 0.33
SILVER_P = 0.33
NOTHING_P = 0.34
PLUS_TWO_FIRST = 0.15
PLUS_TWO_SECOND = 0.10
PLUS_TWO_AFTER = 0.05
START_TICKETS = 3
MAX_TICKETS = 50
DEFAULT_BASELINE_GOLD = 4
DEFAULT_BASELINE_SILVER = 6
PRIZES = frozenset({"gold", "silver", "nothing"})


class RandomLike(Protocol):
    def random(self) -> float: ...


def plus_two_rate(ticket_index: int) -> float:
    """15% on the first ticket, 10% on the second, 5% on every ticket after."""
    if ticket_index <= 0:
        return PLUS_TWO_FIRST
    if ticket_index == 1:
        return PLUS_TWO_SECOND
    return PLUS_TWO_AFTER


def roll_prize(rng: RandomLike) -> str:
    roll = rng.random()
    if roll < GOLD_P:
        return "gold"
    if roll < GOLD_P + SILVER_P:
        return "silver"
    return "nothing"


def roll_ticket(ticket_index: int, rng: RandomLike) -> dict[str, Any]:
    prize = roll_prize(rng)
    plus_two = rng.random() < plus_two_rate(ticket_index)
    if plus_two and prize == "nothing":
        prize = "gold" if rng.random() < 0.5 else "silver"
    return {"prize": prize, "plus_two": bool(plus_two)}


def roll_session(rng: RandomLike | None = None) -> list[dict[str, Any]]:
    """Roll a full extras session, appending a ticket for each +2 hit."""
    rng = rng or random.Random()
    tickets: list[dict[str, Any]] = []
    pending = START_TICKETS
    index = 0
    while pending > 0 and len(tickets) < MAX_TICKETS:
        ticket = roll_ticket(index, rng)
        tickets.append(ticket)
        pending -= 1
        if ticket["plus_two"] and len(tickets) + pending < MAX_TICKETS:
            pending += 1
        index += 1
    return tickets


def tally_extras(tickets: list[dict[str, Any]]) -> tuple[int, int]:
    gold = sum(1 for t in tickets if t.get("prize") == "gold")
    silver = sum(1 for t in tickets if t.get("prize") == "silver")
    return gold, silver


def draw_totals(
    baseline_gold: int,
    baseline_silver: int,
    extra_gold: int,
    extra_silver: int,
) -> tuple[int, int]:
    return (
        max(0, int(baseline_gold)) + max(0, int(extra_gold)),
        max(0, int(baseline_silver)) + max(0, int(extra_silver)),
    )


def normalize_ticket_summary(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:MAX_TICKETS]:
        if not isinstance(item, dict):
            continue
        prize = str(item.get("prize") or "").strip().lower()
        if prize not in PRIZES:
            continue
        out.append({"prize": prize, "plus_two": bool(item.get("plus_two"))})
    return out


def extras_payload(row: Any | None) -> dict[str, Any]:
    tickets = normalize_ticket_summary(getattr(row, "ticket_summary", None) if row else None)
    extra_gold = int(getattr(row, "extra_gold", 0) or 0) if row else 0
    extra_silver = int(getattr(row, "extra_silver", 0) or 0) if row else 0
    if tickets:
        extra_gold, extra_silver = tally_extras(tickets)
    return {
        "extra_gold": extra_gold,
        "extra_silver": extra_silver,
        "tickets": tickets,
        "complete": bool(tickets),
    }


def load_scratch_extras(session: Session, league_slug: str) -> dict[str, Any]:
    from app.site_models import BoostLotteryScratchExtras

    row = session.scalars(
        select(BoostLotteryScratchExtras).where(BoostLotteryScratchExtras.league_slug == league_slug)
    ).first()
    return extras_payload(row)


def save_scratch_extras(
    session: Session,
    league_slug: str,
    *,
    tickets: Any,
    user_id: int | None = None,
) -> dict[str, Any]:
    from app.site_models import BoostLotteryScratchExtras

    normalized = normalize_ticket_summary(tickets)
    extra_gold, extra_silver = tally_extras(normalized)
    now = datetime.utcnow()
    row = session.scalars(
        select(BoostLotteryScratchExtras).where(BoostLotteryScratchExtras.league_slug == league_slug)
    ).first()
    if row is None:
        row = BoostLotteryScratchExtras(
            league_slug=league_slug,
            extra_gold=extra_gold,
            extra_silver=extra_silver,
            ticket_summary=json.dumps(normalized),
            updated_by_user_id=user_id,
            updated_at=now,
        )
        session.add(row)
    else:
        row.extra_gold = extra_gold
        row.extra_silver = extra_silver
        row.ticket_summary = json.dumps(normalized)
        row.updated_by_user_id = user_id
        row.updated_at = now
    return extras_payload(row)


def reset_scratch_extras(
    session: Session,
    league_slug: str,
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    return save_scratch_extras(session, league_slug, tickets=[], user_id=user_id)
