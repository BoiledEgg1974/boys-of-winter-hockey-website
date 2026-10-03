"""Cross-league transfer tool: assets, validation, publish, Discord formatting."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.draft_pick_ownership import draft_pick_asset_dicts
from app.services.gm_notifications import notify_transfer_outcome_proposer
from app.services.trade_tool import (
    _player_row_dict,
    describe_drag_key,
    tradable_drag_keys_for_team,
)
from app.services.transfer_ai_partner import apply_ai_review_to_proposal, evaluate_transfer_proposal
from app.services.transfer_rules import (
    STATUS_COMMISSIONER_DECLINED,
    STATUS_PARTNER_DECLINED,
    STATUS_PENDING_COMMISSIONER,
    STATUS_PENDING_PARTNER,
    STATUS_PUBLISHED,
    bowl_team_budget_snapshot,
    build_player_transfer_context,
    external_leagues_for_transfer,
    external_teams_for_league,
    is_transfer_tool_league,
    load_transfer_rules_config,
    rules_snapshot_for_players,
    transfer_proposal_budget_impact,
)
from app.site_models import GmLeagueMembership, GmTransferProposal, NewsArticle

PLAYER_PENDING_REVIEW_MESSAGE = "This player is already traded pending review."


def parse_compensation_payload(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip() or "{}"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def parse_player_ids(raw: str | list | None) -> list[int]:
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip().isdigit()]
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            arr = json.loads(text)
            return [int(x) for x in arr if str(x).strip().isdigit()]
        except json.JSONDecodeError:
            return []
    return [int(x) for x in text.split(",") if x.strip().isdigit()]


def pending_commissioner_proposal_for_players(
    site_session: Session,
    *,
    league_slug: str,
    player_ids: list[int],
    exclude_proposal_id: int | None = None,
) -> GmTransferProposal | None:
    """Another proposal already awaiting league office approval for one of these players."""
    targets = {int(p) for p in player_ids if p}
    if not targets:
        return None
    rows = site_session.scalars(
        select(GmTransferProposal).where(
            GmTransferProposal.league_slug == league_slug,
            GmTransferProposal.status == STATUS_PENDING_COMMISSIONER,
        )
    ).all()
    for prop in rows:
        if exclude_proposal_id is not None and int(prop.id) == int(exclude_proposal_id):
            continue
        existing = {int(p) for p in parse_player_ids(prop.player_ids_json)}
        if targets & existing:
            return prop
    return None


def pending_review_error_for_players(
    site_session: Session,
    *,
    league_slug: str,
    player_ids: list[int],
    exclude_proposal_id: int | None = None,
) -> str | None:
    if pending_commissioner_proposal_for_players(
        site_session,
        league_slug=league_slug,
        player_ids=player_ids,
        exclude_proposal_id=exclude_proposal_id,
    ):
        return PLAYER_PENDING_REVIEW_MESSAGE
    return None


def player_ids_awaiting_commissioner_review(
    site_session: Session,
    *,
    league_slug: str,
) -> frozenset[int]:
    out: set[int] = set()
    rows = site_session.scalars(
        select(GmTransferProposal).where(
            GmTransferProposal.league_slug == league_slug,
            GmTransferProposal.status == STATUS_PENDING_COMMISSIONER,
        )
    ).all()
    for prop in rows:
        out.update(parse_player_ids(prop.player_ids_json))
    return frozenset(out)


def external_team_has_human_gm(session: Session, league_slug: str, team_id: int) -> bool:
    return external_team_gm_user_id(session, league_slug, team_id) is not None


def external_team_gm_user_id(session: Session, league_slug: str, team_id: int) -> int | None:
    mem = session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(team_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    if mem is None:
        return None
    return int(mem.user_id)


def transfer_roster_for_team(session: Session, team_id: int) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(Player)
            .where(Player.current_team_id == int(team_id), Player.retired.is_(False))
            .order_by(Player.position.nulls_last(), Player.full_name)
        ).all()
    )
    return [_player_row_dict(session, pl, "roster") for pl in rows]


def bowl_sweetener_assets(
    session: Session,
    site_session: Session,
    *,
    team_id: int,
    league_slug: str,
    raw_dir: Path | None,
) -> dict[str, list[dict[str, Any]]]:
    cfg = load_transfer_rules_config(league_slug)
    roster = transfer_roster_for_team(session, int(team_id)) if cfg.allow_player_sweeteners else []
    picks: list[dict[str, Any]] = []
    if cfg.allow_draft_pick_sweeteners:
        picks = draft_pick_asset_dicts(site_session, site_session, league_slug=league_slug, team_id=int(team_id))
    return {"roster": roster, "draft_picks": picks}


def validate_transfer_submission(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    bowl_team_id: int,
    external_team_id: int,
    external_league_fhm_id: int,
    player_ids: list[int],
    compensation: dict[str, Any],
    raw_dir: Path | None,
    exclude_proposal_id: int | None = None,
) -> str | None:
    if not is_transfer_tool_league(league_slug):
        return "Transfer Tool is only available on BOWL-Relegation."
    cfg = load_transfer_rules_config(league_slug)
    picks = list(compensation.get("draft_picks") or [])
    if picks and not cfg.allow_draft_pick_sweeteners:
        return "Draft picks cannot be used as transfer compensation. Offer cash and/or players only."
    if external_league_fhm_id not in cfg.eligible_external_league_fhm_ids:
        return "That external league is not eligible for transfers."
    if int(bowl_team_id) == int(external_team_id):
        return "Select a different club — you cannot transfer a player from your own team."
    bowl_team = session.get(Team, int(bowl_team_id))
    ext_team = session.get(Team, int(external_team_id))
    if not bowl_team or not ext_team:
        return "Invalid team selection."
    from app.services.all_time_records import bowl_nhl_league_ids

    league_ids = frozenset(bowl_nhl_league_ids(session) or (0, 1))
    bid = bowl_team.fhm_league_id
    if bid is not None and int(bid) not in league_ids and int(bid) != 0:
        return "Your team must be a BOWL Upper/Lower franchise."
    if int(ext_team.fhm_league_id or -1) != int(external_league_fhm_id):
        return "External team does not belong to the selected league."
    if not player_ids:
        return "Select at least one player to acquire."
    if len(player_ids) > cfg.max_players_acquired:
        return f"Maximum {cfg.max_players_acquired} player(s) per transfer in this version."
    for pid in player_ids:
        pl = session.get(Player, int(pid))
        if not pl or pl.current_team_id != int(external_team_id):
            return "Selected player is not on the external team roster."
    if len(picks) > cfg.max_sweetener_picks:
        return f"Maximum {cfg.max_sweetener_picks} draft pick sweetener(s)."
    players_out = list(compensation.get("players") or [])
    if players_out and not cfg.allow_player_sweeteners:
        return "Player sweeteners are not allowed."
    if len(players_out) > cfg.max_sweetener_players:
        return f"Maximum {cfg.max_sweetener_players} player sweetener(s)."
    allowed = tradable_drag_keys_for_team(session, int(bowl_team_id), raw_dir, league_slug=league_slug)
    for key in picks + players_out:
        if str(key) not in allowed:
            return f"Invalid sweetener asset: {key}"
    pta = int(compensation.get("pta_transfer_fee") or 0)
    cash = int(compensation.get("cash_sweetener") or 0)
    if cash and not cfg.allow_cash_sweetener:
        return "Cash sweeteners are not allowed."
    budget = bowl_team_budget_snapshot(session, bowl_team_id=int(bowl_team_id), league_slug=league_slug)
    remaining = budget.get("remaining_budget_usd")
    if remaining is not None and (pta + cash) > int(remaining):
        return (
            f"PTA fee plus cash (${pta + cash:,}) exceeds remaining team budget "
            f"(${int(remaining):,} of ${int(budget.get('salary_cap_usd') or 0):,} cap)."
        )
    players = [session.get(Player, int(pid)) for pid in player_ids]
    players = [p for p in players if p]
    snap = rules_snapshot_for_players(
        session,
        players=players,
        external_team=ext_team,
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    required = int(snap.get("required_pta_fee_usd") or 0)
    if pta < required:
        return f"PTA transfer fee must be at least ${required:,}."
    if snap.get("any_blocked"):
        reasons = snap.get("block_reasons") or []
        return reasons[0] if reasons else "Transfer blocked by rules."
    pending_err = pending_review_error_for_players(
        site_session,
        league_slug=league_slug,
        player_ids=player_ids,
        exclude_proposal_id=exclude_proposal_id,
    )
    if pending_err:
        return pending_err
    return None


def validate_transfer_proposer(
    site_session: Session,
    *,
    league_slug: str,
    proposer_user_id: int,
    bowl_team_id: int,
    external_team_id: int,
) -> str | None:
    partner_uid = external_team_gm_user_id(site_session, league_slug, external_team_id)
    if partner_uid is not None and int(partner_uid) == int(proposer_user_id):
        return "You cannot submit a transfer proposal to your own team."
    return None


def user_may_view_transfer_proposal(
    site_session: Session,
    *,
    league_slug: str,
    proposal: GmTransferProposal,
    user_id: int,
    is_admin: bool,
) -> bool:
    if is_admin:
        return True
    if int(proposal.proposer_user_id) == int(user_id):
        return True
    partner_uid = external_team_gm_user_id(site_session, league_slug, int(proposal.external_team_id))
    return partner_uid is not None and int(partner_uid) == int(user_id)


def user_is_transfer_selling_partner(
    site_session: Session,
    *,
    league_slug: str,
    proposal: GmTransferProposal,
    user_id: int,
) -> bool:
    partner_uid = external_team_gm_user_id(site_session, league_slug, int(proposal.external_team_id))
    return partner_uid is not None and int(partner_uid) == int(user_id)


def submit_transfer_proposal(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    proposer_user_id: int,
    bowl_team_id: int,
    external_team_id: int,
    external_league_fhm_id: int,
    player_ids: list[int],
    compensation: dict[str, Any],
    rules_snapshot: dict[str, Any],
    notes: str,
    raw_dir: Path | None,
) -> tuple[GmTransferProposal | None, str | None]:
    err = validate_transfer_proposer(
        site_session,
        league_slug=league_slug,
        proposer_user_id=int(proposer_user_id),
        bowl_team_id=int(bowl_team_id),
        external_team_id=int(external_team_id),
    )
    if err:
        return None, err
    err = validate_transfer_submission(
        session,
        site_session,
        league_slug=league_slug,
        bowl_team_id=int(bowl_team_id),
        external_team_id=int(external_team_id),
        external_league_fhm_id=int(external_league_fhm_id),
        player_ids=player_ids,
        compensation=compensation,
        raw_dir=raw_dir,
    )
    if err:
        return None, err
    prop = GmTransferProposal(
        league_slug=league_slug,
        proposer_user_id=int(proposer_user_id),
        bowl_team_id=int(bowl_team_id),
        external_league_fhm_id=int(external_league_fhm_id),
        external_team_id=int(external_team_id),
        player_ids_json=json.dumps(player_ids),
        compensation_json=json.dumps(compensation),
        rules_snapshot_json=json.dumps(rules_snapshot or {}),
        notes=notes,
        status="pending_ai",
    )
    site_session.add(prop)
    site_session.flush()
    partner_uid = external_team_gm_user_id(site_session, league_slug, int(external_team_id))
    if partner_uid is not None:
        prop.status = STATUS_PENDING_PARTNER
        prop.ai_verdict = "pending_partner"
        prop.ai_rationale = "Awaiting selling GM approval."
        from app.services.gm_notifications import notify_transfer_partner_review

        summary = format_transfer_summary(session, prop)
        notify_transfer_partner_review(
            league_slug,
            partner_user_id=int(partner_uid),
            proposal_id=int(prop.id),
            summary_preview=summary,
        )
    else:
        ai_err = run_ai_review_for_proposal(
            session,
            prop,
            league_slug=league_slug,
            raw_dir=raw_dir,
            site_session=site_session,
        )
        if ai_err:
            site_session.delete(prop)
            site_session.flush()
            return None, ai_err
    return prop, None


def partner_respond_to_transfer(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    proposal: GmTransferProposal,
    partner_user_id: int,
    action: str,
    raw_dir: Path | None,
) -> str | None:
    if proposal.status != STATUS_PENDING_PARTNER:
        return "This proposal is not awaiting partner approval."
    if not user_is_transfer_selling_partner(
        site_session,
        league_slug=league_slug,
        proposal=proposal,
        user_id=int(partner_user_id),
    ):
        return "Only the selling club's GM can respond."
    if action == "decline":
        proposal.status = STATUS_PARTNER_DECLINED
        proposal.ai_verdict = "declined"
        proposal.ai_rationale = "Selling GM declined the transfer offer."
        notify_transfer_outcome_proposer(
            league_slug,
            proposer_user_id=int(proposal.proposer_user_id),
            proposal_id=int(proposal.id),
            title="Transfer declined by selling GM",
            body="The other GM declined your cross-league transfer offer.",
        )
        return None
    if action != "accept":
        return "Unknown action."
    err = validate_transfer_submission(
        session,
        site_session,
        league_slug=league_slug,
        bowl_team_id=int(proposal.bowl_team_id),
        external_team_id=int(proposal.external_team_id),
        external_league_fhm_id=int(proposal.external_league_fhm_id),
        player_ids=parse_player_ids(proposal.player_ids_json),
        compensation=parse_compensation_payload(proposal.compensation_json),
        raw_dir=raw_dir,
    )
    if err:
        return err
    proposal.status = STATUS_PENDING_COMMISSIONER
    proposal.ai_verdict = "accept"
    proposal.ai_rationale = "Selling GM accepted. Awaiting league office approval."
    proposal.ai_acted_at = datetime.utcnow()
    return None


def bowl_main_team_ids(session: Session) -> frozenset[int]:
    from app.services.transfer_rules import bowl_main_league_fhm_ids

    return bowl_main_league_fhm_ids(session)


def run_ai_review_for_proposal(
    session: Session,
    proposal: GmTransferProposal,
    *,
    league_slug: str,
    raw_dir: Path | None,
    site_session: Session | None = None,
) -> str | None:
    review_session = site_session if site_session is not None else session
    pids = parse_player_ids(proposal.player_ids_json)
    pending_err = pending_review_error_for_players(
        review_session,
        league_slug=league_slug,
        player_ids=pids,
        exclude_proposal_id=int(proposal.id),
    )
    if pending_err:
        return pending_err
    comp = parse_compensation_payload(proposal.compensation_json)
    result = evaluate_transfer_proposal(
        session,
        player_ids=pids,
        external_team_id=int(proposal.external_team_id),
        bowl_team_id=int(proposal.bowl_team_id),
        compensation=comp,
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    apply_ai_review_to_proposal(proposal, result)
    return None


def format_compensation_summary(session: Session, compensation: dict[str, Any], bowl_team_id: int) -> str:
    lines: list[str] = []
    pta = int(compensation.get("pta_transfer_fee") or 0)
    cash = int(compensation.get("cash_sweetener") or 0)
    if pta:
        lines.append(f"  • PTA / transfer fee: ${pta:,}")
    if cash:
        lines.append(f"  • Cash sweetener: ${cash:,}")
    for key in compensation.get("draft_picks") or []:
        lines.append(f"  • {describe_drag_key(session, str(key))}")
    for key in compensation.get("players") or []:
        lines.append(f"  • {describe_drag_key(session, str(key))}")
    return "\n".join(lines) if lines else "  • (fees only)"


def format_transfer_budget_impact_lines(impact: dict[str, int | None]) -> list[str]:
    cost = int(impact.get("transfer_cost_usd") or 0)
    wallet_before = impact.get("wallet_remaining_before_usd")
    wallet_after = impact.get("wallet_remaining_after_usd")
    effective_before = impact.get("effective_room_before_usd")
    effective_after = impact.get("effective_room_after_usd")
    if (
        cost <= 0
        and wallet_before is None
        and effective_before is None
    ):
        return []
    lines = ["", "BOWL transfer budget (acquiring team):"]
    if cost > 0:
        lines.append(f"  • PTA + cash on this deal: ${cost:,}")
    if wallet_before is not None and wallet_after is not None:
        lines.append(f"  • Transfer wallet remaining: ${int(wallet_before):,} → ${int(wallet_after):,}")
    if effective_before is not None and effective_after is not None:
        label = (
            "Effective room (cap vs wallet)"
            if impact.get("transfer_wallet_cap_usd") is not None
            else "Cap room"
        )
        lines.append(f"  • {label}: ${int(effective_before):,} → ${int(effective_after):,}")
    return lines


def format_transfer_summary(
    session: Session,
    proposal: GmTransferProposal,
    *,
    include_budget_impact: bool = True,
) -> str:
    bowl_team = session.get(Team, int(proposal.bowl_team_id))
    ext_team = session.get(Team, int(proposal.external_team_id))
    pids = parse_player_ids(proposal.player_ids_json)
    comp = parse_compensation_payload(proposal.compensation_json)
    acquired: list[str] = []
    for pid in pids:
        pl = session.get(Player, int(pid))
        if pl:
            acquired.append(pl.full_name)
    fn = bowl_team.full_display_name() if bowl_team else str(proposal.bowl_team_id)
    tn = ext_team.full_display_name() if ext_team else str(proposal.external_team_id)
    lines = [
        f"Transfer: {fn} acquires from {tn}",
        "",
        "Incoming players:",
    ]
    for name in acquired or ["(none)"]:
        lines.append(f"  • {name}")
    lines.extend(["", f"{fn} sends:", format_compensation_summary(session, comp, int(proposal.bowl_team_id))])
    if include_budget_impact:
        impact = transfer_proposal_budget_impact(
            session,
            league_slug=str(proposal.league_slug),
            bowl_team_id=int(proposal.bowl_team_id),
            compensation_json=proposal.compensation_json,
            proposal_status=str(proposal.status or ""),
        )
        lines.extend(format_transfer_budget_impact_lines(impact))
    return "\n".join(lines)


def format_transfer_discord_body(session: Session, proposal: GmTransferProposal) -> str:
    body = format_transfer_summary(session, proposal)
    notes = (proposal.notes or "").strip()
    parts = [body, "", "Approved by the league office. Roster updates follow future data imports."]
    if notes:
        parts = [body, "", "Notes:", notes, "", parts[-1]]
    return "\n".join(parts)


def publish_transfer_news_articles(
    session: Session,
    *,
    league_slug: str,
    proposal: GmTransferProposal,
    commissioner_user_id: int,
) -> int | None:
    bowl_team = session.get(Team, int(proposal.bowl_team_id))
    title = f"Transfer: {bowl_team.full_display_name() if bowl_team else proposal.bowl_team_id} — external signing"
    body = format_transfer_summary(session, proposal)
    notes = (proposal.notes or "").strip()
    if notes:
        body = body + "\n\nNotes:\n" + notes
    body = body + "\n\nApproved by the league office. Roster updates follow future data imports."
    now = datetime.utcnow()
    existing = session.scalar(
        select(NewsArticle)
        .where(
            NewsArticle.league_slug == league_slug,
            NewsArticle.team_id == int(proposal.bowl_team_id),
            NewsArticle.title == title[:300],
            NewsArticle.category == "transactions",
            NewsArticle.status == "published",
        )
        .order_by(NewsArticle.id.desc())
        .limit(1)
    )
    if existing:
        return int(existing.id)
    article = NewsArticle(
        league_slug=league_slug,
        team_id=int(proposal.bowl_team_id),
        title=title[:300],
        body=body,
        category="transactions",
        author_user_id=int(commissioner_user_id),
        status="published",
        published_at=now,
    )
    session.add(article)
    session.flush()
    return int(article.id)


def publish_transfer_proposal(
    session: Session,
    site_session: Session,
    *,
    league_slug: str,
    proposal: GmTransferProposal,
    commissioner_user_id: int,
    raw_dir: Path | None,
    notify_gms: bool = True,
) -> tuple[int | None, str | None]:
    err = validate_transfer_submission(
        session,
        site_session,
        league_slug=league_slug,
        bowl_team_id=int(proposal.bowl_team_id),
        external_team_id=int(proposal.external_team_id),
        external_league_fhm_id=int(proposal.external_league_fhm_id),
        player_ids=parse_player_ids(proposal.player_ids_json),
        compensation=parse_compensation_payload(proposal.compensation_json),
        raw_dir=raw_dir,
        exclude_proposal_id=int(proposal.id),
    )
    if err:
        return None, err
    if proposal.status != STATUS_PENDING_COMMISSIONER:
        return None, "Proposal is not awaiting commissioner approval."
    article_id = publish_transfer_news_articles(
        session,
        league_slug=league_slug,
        proposal=proposal,
        commissioner_user_id=int(commissioner_user_id),
    )
    proposal.status = STATUS_PUBLISHED
    proposal.commissioner_user_id = int(commissioner_user_id)
    proposal.commissioner_acted_at = datetime.utcnow()
    proposal.commissioner_note = ""
    if notify_gms:
        notify_transfer_outcome_proposer(
            league_slug,
            proposer_user_id=int(proposal.proposer_user_id),
            proposal_id=int(proposal.id),
            title="Transfer approved by league office",
            body="Your cross-league transfer was approved. Apply the move in FHM; roster updates follow CSV imports.",
        )
    return article_id, None


def preview_transfer_fees(
    session: Session,
    *,
    player_ids: list[int],
    external_team_id: int,
    league_slug: str,
    raw_dir: Path | None,
    bowl_team_id: int | None = None,
    site_session: Session | None = None,
) -> dict[str, Any]:
    ext_team = session.get(Team, int(external_team_id))
    if not ext_team:
        return {"error": "Team not found"}
    players = [session.get(Player, int(pid)) for pid in player_ids]
    players = [p for p in players if p]
    snap = rules_snapshot_for_players(
        session,
        players=players,
        external_team=ext_team,
        league_slug=league_slug,
        raw_dir=raw_dir,
    )
    contexts = [
        build_player_transfer_context(
            session,
            player=pl,
            external_team=ext_team,
            league_slug=league_slug,
            raw_dir=raw_dir,
        )
        for pl in players
    ]
    review_session = site_session if site_session is not None else session
    pending_review = bool(
        pending_review_error_for_players(
            review_session,
            league_slug=league_slug,
            player_ids=player_ids,
        )
    )
    return {
        "rules_snapshot": snap,
        "pending_commissioner_review": pending_review,
        "pending_review_message": PLAYER_PENDING_REVIEW_MESSAGE if pending_review else "",
        "players": [
            {
                "id": int(c.player.id),
                "name": c.player.full_name,
                "drag_key": f"player:{c.player.id}",
                "pta_fee_usd": c.pta_fee_usd,
                "blocked": c.blocked,
                "block_reason": c.block_reason,
                "rule_notes": c.rule_notes,
                "age": c.age,
            }
            for c in contexts
        ],
        "required_pta_fee_usd": int(snap.get("required_pta_fee_usd") or 0),
        "salary_cap_usd": int(snap.get("salary_cap_usd") or 0),
        "cap_scale": snap.get("cap_scale"),
        "team_budget": (
            bowl_team_budget_snapshot(session, bowl_team_id=int(bowl_team_id), league_slug=league_slug)
            if bowl_team_id
            else None
        ),
    }


def list_external_leagues(session: Session, league_slug: str) -> list[dict[str, Any]]:
    return external_leagues_for_transfer(session, league_slug)


def list_external_teams(
    session: Session,
    fhm_league_id: int,
    *,
    league_slug: str | None = None,
    site_session: Session | None = None,
) -> list[dict[str, Any]]:
    teams = external_teams_for_league(session, int(fhm_league_id))
    slug = (league_slug or "").strip()
    out: list[dict[str, Any]] = []
    for t in teams:
        partner_uid = None
        if slug and site_session is not None:
            partner_uid = external_team_gm_user_id(site_session, slug, int(t.id))
        out.append(
            {
                "team_id": int(t.id),
                "name": t.full_display_name(),
                "abbreviation": t.abbreviation or "",
                "fhm_league_id": int(t.fhm_league_id or 0),
                "has_human_gm": partner_uid is not None,
            }
        )
    return out
