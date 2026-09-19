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
    STATUS_PENDING_COMMISSIONER,
    STATUS_PUBLISHED,
    build_player_transfer_context,
    external_leagues_for_transfer,
    external_teams_for_league,
    is_transfer_tool_league,
    load_transfer_rules_config,
    rules_snapshot_for_players,
)
from app.site_models import GmLeagueMembership, GmTransferProposal, NewsArticle


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


def external_team_has_human_gm(session: Session, league_slug: str, team_id: int) -> bool:
    mem = session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(team_id),
            GmLeagueMembership.status == "active",
        )
    )
    return mem is not None


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
    roster = transfer_roster_for_team(session, int(team_id))
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
) -> str | None:
    if not is_transfer_tool_league(league_slug):
        return "Transfer Tool is only available on BOWL-Relegation."
    cfg = load_transfer_rules_config(league_slug)
    if external_league_fhm_id not in cfg.eligible_external_league_fhm_ids:
        return "That external league is not eligible for transfers."
    if external_team_has_human_gm(site_session, league_slug, external_team_id):
        return "That team has a human GM — negotiate directly, not through the AI transfer tool."
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
    picks = list(compensation.get("draft_picks") or [])
    if len(picks) > cfg.max_sweetener_picks:
        return f"Maximum {cfg.max_sweetener_picks} draft pick sweetener(s)."
    players_out = list(compensation.get("players") or [])
    if len(players_out) > cfg.max_sweetener_players:
        return f"Maximum {cfg.max_sweetener_players} player sweetener(s)."
    allowed = tradable_drag_keys_for_team(session, int(bowl_team_id), raw_dir, league_slug=league_slug)
    for key in picks + players_out:
        if str(key) not in allowed:
            return f"Invalid sweetener asset: {key}"
    pta = int(compensation.get("pta_transfer_fee") or 0)
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
) -> None:
    comp = parse_compensation_payload(proposal.compensation_json)
    pids = parse_player_ids(proposal.player_ids_json)
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


def format_transfer_summary(session: Session, proposal: GmTransferProposal) -> str:
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
    return {
        "rules_snapshot": snap,
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
    }


def list_external_leagues(session: Session, league_slug: str) -> list[dict[str, Any]]:
    return external_leagues_for_transfer(session, league_slug)


def list_external_teams(session: Session, fhm_league_id: int) -> list[dict[str, Any]]:
    teams = external_teams_for_league(session, int(fhm_league_id))
    return [
        {
            "team_id": int(t.id),
            "name": t.full_display_name(),
            "abbreviation": t.abbreviation or "",
            "fhm_league_id": int(t.fhm_league_id or 0),
        }
        for t in teams
    ]
