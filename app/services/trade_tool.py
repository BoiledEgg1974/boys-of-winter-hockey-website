"""BOWL trade ledger: asset lists, validation, and published news text (no roster DB moves)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth_login import ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, has_admin_role
from app.models import DraftPick, Player, Prospect, Team
from app.site_models import GmTradeProposal, NewsArticle, User

MAX_ASSETS_PER_SIDE = 5

STATUS_PENDING_PARTNER = "pending_partner"
STATUS_PARTNER_DECLINED = "partner_declined"
STATUS_PENDING_COMMISSIONER = "pending_commissioner"
STATUS_COMMISSIONER_DECLINED = "commissioner_declined"
STATUS_PUBLISHED = "published"


def league_commissioner_user_ids(session: Session) -> list[int]:
    users = session.scalars(select(User).where(User.revoked_at.is_(None))).all()
    ids = [u.id for u in users if has_admin_role(u, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)]
    if ids:
        return ids
    return [u.id for u in users if getattr(u, "is_admin", False)]


def _roster_player_ids(session: Session, team_id: int) -> set[int]:
    rows = session.scalars(select(Player.id).where(Player.current_team_id == team_id)).all()
    return {int(r) for r in rows}


def trade_assets_for_team(session: Session, team_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return grouped tradeable assets for one team (league DB)."""
    roster_ids = _roster_player_ids(session, team_id)
    roster = list(
        session.scalars(
            select(Player)
            .where(Player.current_team_id == team_id)
            .order_by(Player.position.nulls_last(), Player.full_name)
        ).all()
    )
    prospect_rows = session.scalars(
        select(Prospect)
        .options(joinedload(Prospect.player))
        .where(Prospect.team_id == team_id)
        .order_by(Prospect.id)
    ).all()
    prospects_out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pr in prospect_rows:
        pl = pr.player
        if not pl or not pl.id:
            continue
        if int(pl.id) in roster_ids:
            continue
        if int(pl.id) in seen:
            continue
        seen.add(int(pl.id))
        pos = (pl.position or "").strip() or "—"
        prospects_out.append(
            {
                "kind": "player",
                "id": int(pl.id),
                "drag_key": f"player:{pl.id}",
                "label": pl.full_name or "",
                "position": pos,
                "section": "unsigned",
            }
        )
    roster_out = []
    for pl in roster:
        pos = (pl.position or "").strip() or "—"
        roster_out.append(
            {
                "kind": "player",
                "id": int(pl.id),
                "drag_key": f"player:{pl.id}",
                "label": pl.full_name or "",
                "position": pos,
                "section": "roster",
            }
        )
    picks = list(
        session.scalars(
            select(DraftPick)
            .options(joinedload(DraftPick.draft))
            .where(DraftPick.team_id == team_id, DraftPick.player_id.is_(None))
            .order_by(DraftPick.draft_year.nulls_last(), DraftPick.round.nulls_last(), DraftPick.overall_pick)
        ).all()
    )
    picks_out: list[dict[str, Any]] = []
    for pk in picks:
        dr = pk.draft
        lab_parts: list[str] = []
        if dr and (dr.label or "").strip():
            lab_parts.append(str(dr.label).strip())
        if pk.draft_year is not None:
            lab_parts.append(str(pk.draft_year))
        if pk.round is not None:
            lab_parts.append(f"R{pk.round}")
        if pk.overall_pick is not None:
            lab_parts.append(f"#{pk.overall_pick}")
        label = " · ".join(lab_parts) if lab_parts else f"Pick #{pk.id}"
        picks_out.append(
            {
                "kind": "pick",
                "id": int(pk.id),
                "drag_key": f"pick:{pk.id}",
                "label": label,
                "position": "PICK",
                "section": "pick",
            }
        )
    return {"roster": roster_out, "unsigned": prospects_out, "picks": picks_out}


def _owned_drag_keys(session: Session, team_id: int) -> set[str]:
    s = trade_assets_for_team(session, team_id)
    out: set[str] = set()
    for group in ("roster", "unsigned", "picks"):
        for it in s.get(group, []):
            out.add(str(it["drag_key"]))
    return out


def parse_ledger_payload(raw: str | None) -> tuple[list[str], list[str]]:
    """Return (from_left_to_right, from_right_to_left) as drag_key strings."""
    if not raw or not str(raw).strip():
        return [], []
    try:
        data = json.loads(raw)
    except Exception:
        return [], []
    if not isinstance(data, dict):
        return [], []
    a = data.get("from_left_to_right") or data.get("left_to_right")
    b = data.get("from_right_to_left") or data.get("right_to_left")
    if not isinstance(a, list):
        a = []
    if not isinstance(b, list):
        b = []
    out_a = [str(x).strip() for x in a if str(x).strip()]
    out_b = [str(x).strip() for x in b if str(x).strip()]
    return out_a, out_b


def validate_ledger(
    session: Session, from_team_id: int, to_team_id: int, left_out: list[str], right_out: list[str]
) -> str | None:
    if len(left_out) > MAX_ASSETS_PER_SIDE or len(right_out) > MAX_ASSETS_PER_SIDE:
        return f"Each side may include at most {MAX_ASSETS_PER_SIDE} assets."
    left_owned = _owned_drag_keys(session, from_team_id)
    right_owned = _owned_drag_keys(session, to_team_id)
    for k in left_out:
        if k not in left_owned:
            return "One or more assets leaving your team are not valid for your roster."
    for k in right_out:
        if k not in right_owned:
            return "One or more assets from the partner team are not valid."
    if not left_out and not right_out:
        return "Add at least one asset to one side of the ledger."
    return None


def describe_drag_key(session: Session, drag_key: str) -> str:
    if ":" not in drag_key:
        return drag_key
    kind, _, rest = drag_key.partition(":")
    try:
        eid = int(rest)
    except ValueError:
        return drag_key
    if kind == "player":
        pl = session.get(Player, eid)
        if pl:
            pos = (pl.position or "").strip() or "—"
            return f"{pos} {pl.full_name}".strip()
        return f"Player #{eid}"
    if kind == "pick":
        pk = session.get(DraftPick, eid)
        if pk:
            dr = pk.draft
            parts: list[str] = []
            if dr and (dr.label or "").strip():
                parts.append(str(dr.label).strip())
            if pk.draft_year is not None:
                parts.append(str(pk.draft_year))
            if pk.round is not None:
                parts.append(f"R{pk.round}")
            if pk.overall_pick is not None:
                parts.append(f"#{pk.overall_pick}")
            return " · ".join(parts) if parts else f"Pick #{eid}"
        return f"Pick #{eid}"
    return drag_key


def format_ledger_summary(
    session: Session, from_team: Team | None, to_team: Team | None, left_out: list[str], right_out: list[str]
) -> str:
    fn = from_team.full_display_name() if from_team else "Team A"
    tn = to_team.full_display_name() if to_team else "Team B"
    lines: list[str] = [f"{fn} sends to {tn}:"]
    if left_out:
        for k in left_out:
            lines.append(f"  • {describe_drag_key(session, k)}")
    else:
        lines.append("  • (none)")
    lines.append("")
    lines.append(f"{tn} sends to {fn}:")
    if right_out:
        for k in right_out:
            lines.append(f"  • {describe_drag_key(session, k)}")
    else:
        lines.append("  • (none)")
    return "\n".join(lines)


def format_trade_news_body(session: Session, proposal: GmTradeProposal, from_team: Team | None, to_team: Team | None) -> str:
    left_out, right_out = parse_ledger_payload(proposal.ledger_json)
    summary = format_ledger_summary(session, from_team, to_team, left_out, right_out)
    notes = (proposal.notes or "").strip()
    parts = [summary, "", "Approved by the league office. Roster updates follow future data imports."]
    if notes:
        parts = [summary, "", "Notes & conditions (as submitted):", notes, "", parts[-1]]
    return "\n".join(parts)


def publish_trade_news_articles(
    session: Session,
    *,
    league_slug: str,
    proposal: GmTradeProposal,
    commissioner_user_id: int,
) -> tuple[int | None, int | None]:
    """Two published transaction articles (one per team) for team pages + Around the League."""
    from_team = session.get(Team, proposal.from_team_id)
    to_team = session.get(Team, proposal.to_team_id)
    title = (
        f"Trade: {from_team.full_display_name() if from_team else proposal.from_team_id} "
        f"↔ {to_team.full_display_name() if to_team else proposal.to_team_id}"
    )
    body = format_trade_news_body(session, proposal, from_team, to_team)
    now = datetime.utcnow()
    a1 = NewsArticle(
        league_slug=league_slug,
        team_id=int(proposal.from_team_id),
        title=title[:300],
        body=body,
        category="transactions",
        author_user_id=commissioner_user_id,
        status="published",
        published_at=now,
        ap_awarded=False,
    )
    a2 = NewsArticle(
        league_slug=league_slug,
        team_id=int(proposal.to_team_id),
        title=title[:300],
        body=body,
        category="transactions",
        author_user_id=commissioner_user_id,
        status="published",
        published_at=now,
        ap_awarded=False,
    )
    session.add(a1)
    session.add(a2)
    session.flush()
    return int(a1.id), int(a2.id)
