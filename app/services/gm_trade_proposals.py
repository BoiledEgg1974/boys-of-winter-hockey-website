"""GM-to-GM trade proposals (partner approve → commissioner publish)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team
from app.services.gm_notifications import (
    notify_trade_outcome_partner,
    notify_trade_outcome_proposer,
    notify_trade_proposal_commissioners,
    notify_trade_proposal_partner,
)
from app.services.trade_tool import (
    STATUS_PARTNER_DECLINED,
    STATUS_PENDING_COMMISSIONER,
    STATUS_PENDING_PARTNER,
    format_ledger_summary,
    gm_user_id_for_team,
    league_commissioner_user_ids,
    parse_ledger_payload,
    trade_tool_draft_round_cap,
    validate_ledger,
)
from app.services.trade_transfer_pta import (
    format_transfer_compensation_summary,
    gm_trade_proposals_enabled,
    parse_trade_ledger_object,
    run_trade_pta_dry_run,
)
from app.site_models import GmLeagueMembership, GmTradeProposal


def user_may_view_trade_proposal(
    proposal: GmTradeProposal,
    *,
    user_id: int,
    is_admin: bool,
) -> bool:
    if is_admin:
        return True
    uid = int(user_id)
    return uid in (int(proposal.from_user_id), int(proposal.to_user_id))


def submit_gm_trade_proposal(
    session: Session,
    *,
    league_slug: str,
    proposer_user_id: int,
    from_team_id: int,
    to_team_id: int,
    ledger_raw: str,
    notes: str,
    raw_dir: Path | None,
    draft_round_cap: int | None = None,
) -> tuple[GmTradeProposal | None, str | None]:
    if not gm_trade_proposals_enabled(league_slug):
        return None, "GM trade proposals are not enabled on this league."
    if int(from_team_id) == int(to_team_id):
        return None, "Choose a different trading partner."
    mem = session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.user_id == int(proposer_user_id),
            GmLeagueMembership.team_id == int(from_team_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    if mem is None:
        return None, "You must propose from your active team."
    partner_uid = gm_user_id_for_team(session, league_slug, int(to_team_id))
    if partner_uid is None:
        return None, "That team does not have an active human GM."
    if int(partner_uid) == int(proposer_user_id):
        return None, "You cannot trade with yourself."
    left_out, right_out = parse_ledger_payload(ledger_raw)
    cap = draft_round_cap if draft_round_cap is not None else trade_tool_draft_round_cap(session, league_slug)
    err = validate_ledger(
        session,
        int(from_team_id),
        int(to_team_id),
        left_out,
        right_out,
        raw_dir=raw_dir,
        league_slug=league_slug,
        draft_round_cap=cap,
    )
    if err:
        return None, err
    pta = run_trade_pta_dry_run(
        session,
        league_slug=league_slug,
        left_team_id=int(from_team_id),
        right_team_id=int(to_team_id),
        ledger_raw=ledger_raw,
        raw_dir=raw_dir,
        ledger_error=None,
    )
    if not pta.get("valid"):
        msg = "\n".join(pta.get("errors") or []) or pta.get("summary") or "Transfer fee validation failed."
        return None, msg
    payload = parse_trade_ledger_object(ledger_raw)
    prop = GmTradeProposal(
        league_slug=league_slug,
        from_user_id=int(proposer_user_id),
        from_team_id=int(from_team_id),
        to_user_id=int(partner_uid),
        to_team_id=int(to_team_id),
        status=STATUS_PENDING_PARTNER,
        ledger_json=json.dumps(payload),
        notes=(notes or "")[:8000],
    )
    session.add(prop)
    session.flush()
    from_team = session.get(Team, int(from_team_id))
    to_team = session.get(Team, int(to_team_id))
    summary = format_ledger_summary(
        session,
        from_team,
        to_team,
        payload["from_left_to_right"],
        payload["from_right_to_left"],
        league_slug=league_slug,
    )
    pta_line = format_transfer_compensation_summary(payload.get("transfer_compensation") or {})
    if pta_line:
        summary = summary + "\n\n" + pta_line
    notify_trade_proposal_partner(
        league_slug,
        partner_user_id=int(partner_uid),
        proposal_id=int(prop.id),
        summary_preview=summary,
    )
    return prop, None


def partner_respond_gm_trade(
    session: Session,
    *,
    league_slug: str,
    proposal: GmTradeProposal,
    partner_user_id: int,
    action: str,
    raw_dir: Path | None,
) -> str | None:
    if proposal.status != STATUS_PENDING_PARTNER:
        return "This proposal is not awaiting your approval."
    if int(proposal.to_user_id) != int(partner_user_id):
        return "Only the receiving GM can respond."
    if action == "decline":
        proposal.status = STATUS_PARTNER_DECLINED
        proposal.partner_acted_at = datetime.utcnow()
        notify_trade_outcome_proposer(
            league_slug,
            proposer_user_id=int(proposal.from_user_id),
            proposal_id=int(proposal.id),
            title="Trade declined by partner",
            body="Your trade partner declined the proposal.",
        )
        return None
    if action not in ("approve", "accept"):
        return "Unknown action."
    pta = run_trade_pta_dry_run(
        session,
        league_slug=league_slug,
        left_team_id=int(proposal.from_team_id),
        right_team_id=int(proposal.to_team_id),
        ledger_raw=proposal.ledger_json,
        raw_dir=raw_dir,
        ledger_error=None,
    )
    if not pta.get("valid"):
        return (pta.get("errors") or ["Transfer fee validation failed."])[0]
    left_out, right_out = parse_ledger_payload(proposal.ledger_json)
    err = validate_ledger(
        session,
        int(proposal.from_team_id),
        int(proposal.to_team_id),
        left_out,
        right_out,
        raw_dir=raw_dir,
        league_slug=league_slug,
    )
    if err:
        return err
    proposal.status = STATUS_PENDING_COMMISSIONER
    proposal.partner_acted_at = datetime.utcnow()
    from_team = session.get(Team, int(proposal.from_team_id))
    to_team = session.get(Team, int(proposal.to_team_id))
    summary = format_ledger_summary(
        session,
        from_team,
        to_team,
        left_out,
        right_out,
        league_slug=league_slug,
    )
    payload = parse_trade_ledger_object(proposal.ledger_json)
    pta_line = format_transfer_compensation_summary(payload.get("transfer_compensation") or {})
    if pta_line:
        summary = summary + "\n\n" + pta_line
    notify_trade_proposal_commissioners(
        league_slug,
        commissioner_user_ids=league_commissioner_user_ids(session),
        proposal_id=int(proposal.id),
        summary_preview=summary,
    )
    notify_trade_outcome_proposer(
        league_slug,
        proposer_user_id=int(proposal.from_user_id),
        proposal_id=int(proposal.id),
        title="Partner approved — awaiting league office",
        body="Your trade partner accepted. The league office will publish or deny the deal.",
    )
    return None
