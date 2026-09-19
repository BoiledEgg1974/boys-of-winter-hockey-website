"""Deterministic AI partner review for cross-league transfer proposals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.transfer_rules import (
    PlayerTransferContext,
    STATUS_AI_COUNTER,
    STATUS_AI_DECLINED,
    STATUS_PENDING_COMMISSIONER,
    build_player_transfer_context,
    load_transfer_rules_config,
)
from app.services.transfer_valuation import (
    compensation_offer_value_usd,
    external_club_minimum_ask_usd,
)


@dataclass
class TransferAiReviewResult:
    ai_status: str
    ai_verdict: str
    ai_counter_json: dict[str, Any]
    ai_rationale: str
    proposal_status: str
    offer_value_usd: int
    minimum_ask_usd: int
    breakdown: dict[str, Any]


def evaluate_transfer_proposal(
    session: Session,
    *,
    player_ids: list[int],
    external_team_id: int,
    bowl_team_id: int,
    compensation: dict[str, Any],
    league_slug: str,
    raw_dir=None,
) -> TransferAiReviewResult:
    from app.models import Player, Team

    cfg = load_transfer_rules_config(league_slug)
    ai_cfg = cfg.ai_partner or {}
    accept_threshold = float(ai_cfg.get("accept_threshold") or 0.97)
    counter_threshold = float(ai_cfg.get("counter_threshold") or 0.72)
    decline_below = float(ai_cfg.get("decline_below") or 0.55)

    external_team = session.get(Team, int(external_team_id))
    if not external_team:
        return TransferAiReviewResult(
            ai_status="error",
            ai_verdict="decline",
            ai_counter_json={},
            ai_rationale="External team not found.",
            proposal_status=STATUS_AI_DECLINED,
            offer_value_usd=0,
            minimum_ask_usd=0,
            breakdown={},
        )

    players: list[Player] = []
    for pid in player_ids:
        pl = session.get(Player, int(pid))
        if pl:
            players.append(pl)
    if not players:
        return TransferAiReviewResult(
            ai_status="error",
            ai_verdict="decline",
            ai_counter_json={},
            ai_rationale="No valid players selected.",
            proposal_status=STATUS_AI_DECLINED,
            offer_value_usd=0,
            minimum_ask_usd=0,
            breakdown={},
        )

    contexts: list[PlayerTransferContext] = [
        build_player_transfer_context(
            session,
            player=pl,
            external_team=external_team,
            league_slug=league_slug,
            raw_dir=raw_dir,
        )
        for pl in players
    ]
    blocked = [c for c in contexts if c.blocked]
    if blocked:
        reason = blocked[0].block_reason or "Transfer blocked by league rules."
        return TransferAiReviewResult(
            ai_status="blocked",
            ai_verdict="decline",
            ai_counter_json={},
            ai_rationale=reason,
            proposal_status=STATUS_AI_DECLINED,
            offer_value_usd=0,
            minimum_ask_usd=0,
            breakdown={"blocked_players": [c.player.full_name for c in blocked]},
        )

    required_pta = sum(c.pta_fee_usd for c in contexts)
    comp = dict(compensation)
    comp.setdefault("pta_transfer_fee", required_pta)
    if int(comp.get("pta_transfer_fee") or 0) < required_pta:
        comp["pta_transfer_fee"] = required_pta

    offer_usd, offer_breakdown = compensation_offer_value_usd(
        session,
        compensation=comp,
        bowl_team_id=int(bowl_team_id),
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    minimum_usd, ask_breakdown = external_club_minimum_ask_usd(
        session, contexts, league_slug=league_slug
    )
    ratio = (offer_usd / minimum_usd) if minimum_usd > 0 else (1.0 if offer_usd > 0 else 0.0)

    team_name = external_team.full_display_name()
    player_names = ", ".join(pl.full_name for pl in players)
    breakdown = {
        "offer": offer_breakdown,
        "minimum_ask": ask_breakdown,
        "ratio": round(ratio, 4),
        "required_pta_usd": required_pta,
    }

    if ratio >= accept_threshold:
        rationale = (
            f"{team_name} accepts the package for {player_names}. "
            f"Your offer (${offer_usd:,}) meets their valuation (${minimum_usd:,}). "
            "Proposal forwarded to the league office for final approval."
        )
        return TransferAiReviewResult(
            ai_status="accepted",
            ai_verdict="accept",
            ai_counter_json={},
            ai_rationale=rationale,
            proposal_status=STATUS_PENDING_COMMISSIONER,
            offer_value_usd=offer_usd,
            minimum_ask_usd=minimum_usd,
            breakdown=breakdown,
        )

    if ratio >= counter_threshold:
        shortfall = max(0, minimum_usd - offer_usd)
        suggested_cash = int(shortfall * 1.05)
        counter = {
            "minimum_ask_usd": minimum_usd,
            "current_offer_usd": offer_usd,
            "suggested_additional_cash_usd": suggested_cash,
            "suggested_compensation": {
                **comp,
                "cash_sweetener": int(comp.get("cash_sweetener") or 0) + suggested_cash,
                "pta_transfer_fee": required_pta,
            },
        }
        rationale = (
            f"{team_name} is interested in {player_names} but wants more compensation. "
            f"Current offer: ${offer_usd:,}. Their floor: ${minimum_usd:,}. "
            f"Consider adding roughly ${suggested_cash:,} in cash or a player of similar value."
        )
        return TransferAiReviewResult(
            ai_status="counter",
            ai_verdict="counter",
            ai_counter_json=counter,
            ai_rationale=rationale,
            proposal_status=STATUS_AI_COUNTER,
            offer_value_usd=offer_usd,
            minimum_ask_usd=minimum_usd,
            breakdown=breakdown,
        )

    if ratio < decline_below:
        rationale = (
            f"{team_name} declines — the offer (${offer_usd:,}) is too far below their "
            f"minimum (${minimum_usd:,}) for {player_names}. They will not sell core assets at a discount."
        )
    else:
        rationale = (
            f"{team_name} declines — {player_names} is not available at this price. "
            f"Offer ${offer_usd:,} vs minimum ${minimum_usd:,}."
        )
    return TransferAiReviewResult(
        ai_status="declined",
        ai_verdict="decline",
        ai_counter_json={},
        ai_rationale=rationale,
        proposal_status=STATUS_AI_DECLINED,
        offer_value_usd=offer_usd,
        minimum_ask_usd=minimum_usd,
        breakdown=breakdown,
    )


def apply_ai_review_to_proposal(proposal, result: TransferAiReviewResult) -> None:
    """Mutate GmTransferProposal in place."""
    import json

    proposal.ai_status = result.ai_status
    proposal.ai_verdict = result.ai_verdict
    proposal.ai_counter_json = json.dumps(result.ai_counter_json or {})
    proposal.ai_rationale = (result.ai_rationale or "")[:8000]
    proposal.status = result.proposal_status
    proposal.ai_acted_at = datetime.utcnow()
