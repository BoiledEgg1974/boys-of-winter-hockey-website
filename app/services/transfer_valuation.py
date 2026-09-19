"""Player and compensation valuation for cross-league transfer negotiation."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player, PlayerInjury
from app.services.draft_pick_values import ROUND_AVERAGE_VALUE, perri_pick_value_for_round
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row
from app.services.transfer_rules import PlayerTransferContext, TransferRulesConfig, load_transfer_rules_config


def _age_multiplier(age: int | None) -> float:
    if age is None:
        return 0.85
    if age <= 21:
        return 1.05
    if age <= 24:
        return 1.0
    if age <= 28:
        return 0.95
    if age <= 31:
        return 0.75
    if age <= 34:
        return 0.55
    return 0.35


def player_asset_value(
    session: Session,
    player: Player,
    *,
    age: int | None = None,
) -> float:
    """0–100 scale fair value for external club asset protection."""
    rr = get_player_ratings_row(getattr(player, "fhm_player_id", None))
    ovr = compute_player_overall_100(
        player.overall_ability,
        player.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(player),
    )
    base = float(ovr) if ovr is not None else 0.0
    if base <= 0 and player.overall_ability is not None:
        base = float(player.overall_ability)
    if base <= 0 and player.overall_potential is not None:
        base = float(player.overall_potential) * 0.85
    pot = float(player.overall_potential or 0)
    if age is not None and age <= 25 and pot > base:
        base = base * 0.75 + pot * 0.25
    base *= _age_multiplier(age)
    injured = session.scalar(
        select(func.count())
        .select_from(PlayerInjury)
        .where(PlayerInjury.player_id == int(player.id))
    )
    if int(injured or 0) > 0:
        base *= 0.88
    return round(max(0.0, min(100.0, base)), 2)


def _pick_usd_value(pick_key: str, cfg: TransferRulesConfig) -> float:
    mult = float(cfg.pick_value_to_usd_multiplier or 50000)
    parts = (pick_key or "").split(":")
    if len(parts) < 2:
        return 0.0
    kind = parts[0]
    if kind == "mpleft" and len(parts) >= 2:
        try:
            rnd = int(parts[1])
        except ValueError:
            return 0.0
        val = perri_pick_value_for_round(round_no=rnd) or ROUND_AVERAGE_VALUE.get(rnd, 5.0)
        return float(val) * mult
    if kind == "dpick":
        return mult * 8.0
    return mult * 5.0


def compensation_offer_value_usd(
    session: Session,
    *,
    compensation: dict[str, Any],
    bowl_team_id: int,
    league_slug: str,
    raw_dir=None,
) -> tuple[int, dict[str, Any]]:
    """Total USD value of BOWL-side compensation package."""
    cfg = load_transfer_rules_config(league_slug)
    pta = int(compensation.get("pta_transfer_fee") or compensation.get("required_pta_fee_usd") or 0)
    cash = int(compensation.get("cash_sweetener") or 0)
    picks = list(compensation.get("draft_picks") or [])
    players = list(compensation.get("players") or [])
    pick_usd = sum(_pick_usd_value(str(k), cfg) for k in picks)
    player_usd = 0
    player_details: list[dict[str, Any]] = []
    for key in players:
        if not str(key).startswith("player:"):
            continue
        try:
            pid = int(str(key).split(":", 1)[1])
        except (ValueError, IndexError):
            continue
        pl = session.get(Player, pid)
        if not pl or pl.current_team_id != bowl_team_id:
            continue
        from app.services.transfer_rules import _player_age

        age = _player_age(session, pl)
        val = player_asset_value(session, pl, age=age)
        usd = int(val * 10000)
        player_usd += usd
        player_details.append({"player_id": pid, "name": pl.full_name, "value_usd": usd})
    total = int(pta + cash + pick_usd + player_usd)
    breakdown = {
        "pta_transfer_fee": pta,
        "cash_sweetener": cash,
        "pick_value_usd": int(pick_usd),
        "player_value_usd": int(player_usd),
        "player_details": player_details,
        "total_usd": total,
    }
    return total, breakdown


def external_club_minimum_ask_usd(
    session: Session,
    contexts: list[PlayerTransferContext],
    *,
    league_slug: str,
) -> tuple[int, dict[str, Any]]:
    """Minimum compensation external AI club expects."""
    cfg = load_transfer_rules_config(league_slug)
    ai = cfg.ai_partner or {}
    sell_mult = float(ai.get("league_sell_multiplier") or 0.35)
    star_threshold = float(ai.get("star_ovr_threshold") or 78)
    star_tax = int(ai.get("star_tax_usd") or 750000)
    year_premium = int(ai.get("contract_year_premium_usd") or 125000)
    pta_total = sum(c.pta_fee_usd for c in contexts)
    player_fair = 0
    details: list[dict[str, Any]] = []
    for ctx in contexts:
        val = player_asset_value(session, ctx.player, age=ctx.age)
        fair_usd = int(val * 10000 * sell_mult)
        if val >= star_threshold:
            fair_usd += star_tax
        if ctx.contract_years_remaining and ctx.contract_years_remaining > 0:
            fair_usd += year_premium * int(ctx.contract_years_remaining)
        if ctx.average_salary and ctx.average_salary >= 2_000_000:
            fair_usd += int(ctx.average_salary * 0.15)
        player_fair += fair_usd
        details.append(
            {
                "player_id": int(ctx.player.id),
                "name": ctx.player.full_name,
                "asset_value": val,
                "fair_usd": fair_usd,
            }
        )
    minimum = int(pta_total + player_fair)
    return minimum, {
        "pta_total_usd": pta_total,
        "player_fair_usd": player_fair,
        "minimum_ask_usd": minimum,
        "players": details,
    }
