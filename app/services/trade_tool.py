"""BOWL trade ledger: asset lists, validation, and published news text (no roster DB moves)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from flask import current_app, has_request_context

from app.auth_login import ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, has_admin_role
from app.models import DraftPick, Player, Prospect, Team
from app.services.free_agents import player_ids_from_player_rights_csv_for_team
from app.services.league_rules import rule_int
from app.site_models import GmTradeProposal, NewsArticle, User

MAX_ASSETS_PER_SIDE = 5

MANUAL_PICK_PREFIX_LEFT = "mpleft"
MANUAL_PICK_PREFIX_RIGHT = "mpright"

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


def trade_tool_draft_round_cap(session: Session, league_slug: str) -> int:
    """Max draft round (1..N) for manual pick chips; commissioner rule ``trade_tool_draft_rounds``."""
    n = rule_int(session, league_slug, "trade_tool_draft_rounds", default=8)
    return max(1, min(32, int(n)))


def _roster_player_ids(session: Session, team_id: int) -> set[int]:
    rows = session.scalars(select(Player.id).where(Player.current_team_id == team_id)).all()
    return {int(r) for r in rows}


def enrich_trade_player_row(session: Session, pl: Player, row: dict[str, Any]) -> None:
    """Add display fields for Trade Tool UI (ratings, positions, headshot path, pill CSS)."""
    if not has_request_context():
        return
    from pathlib import Path

    from app.services.player_headshot import resolve_player_headshot_static_filename
    from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
    from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label

    rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None))
    is_g = player_is_goalie_for_overall(pl)
    ovr = compute_player_overall_100(
        pl.overall_ability, pl.overall_potential, rr, is_goalie=is_g
    )
    row["positions"] = player_positions_display_label(pl)
    row["abi"] = float(pl.overall_ability) if pl.overall_ability is not None else None
    row["pot"] = float(pl.overall_potential) if pl.overall_potential is not None else None
    row["ovr"] = ovr
    static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        str(current_app.config.get("PLAYER_HEADSHOTS_REL_DIR") or "players"),
    )
    row["headshot_rel"] = rel
    try:
        rating_style = current_app.jinja_env.filters["rating_pill_style"]
        row["abi_style"] = rating_style(pl.overall_ability)
        row["pot_style"] = rating_style(pl.overall_potential)
    except Exception:
        row["abi_style"] = ""
        row["pot_style"] = ""


def _player_row_dict(session: Session, pl: Player, section: str) -> dict[str, Any]:
    pos = (pl.position or "").strip() or "—"
    row: dict[str, Any] = {
        "kind": "player",
        "id": int(pl.id),
        "drag_key": f"player:{pl.id}",
        "label": pl.full_name or "",
        "position": pos,
        "section": section,
    }
    enrich_trade_player_row(session, pl, row)
    return row


def trade_assets_for_team(
    session: Session, team_id: int, *, raw_dir: Path | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Roster + rights (DB prospects + ``player_rights.csv`` for this league). No DB draft picks."""
    tm = session.get(Team, team_id)
    if not tm:
        return {"roster": [], "unsigned": []}
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
    unsigned: list[dict[str, Any]] = []
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
        unsigned.append(_player_row_dict(session, pl, "unsigned"))
    if raw_dir is not None:
        for pid in player_ids_from_player_rights_csv_for_team(session, raw_dir, tm):
            if pid in roster_ids or pid in seen:
                continue
            pl = session.get(Player, pid)
            if not pl or pl.retired:
                continue
            seen.add(int(pid))
            unsigned.append(_player_row_dict(session, pl, "rights"))
    unsigned.sort(key=lambda r: (str(r.get("label") or "").lower(), int(r.get("id") or 0)))
    roster_out: list[dict[str, Any]] = []
    for pl in roster:
        roster_out.append(_player_row_dict(session, pl, "roster"))
    return {"roster": roster_out, "unsigned": unsigned}


def player_drag_keys_for_team(session: Session, team_id: int, raw_dir: Path | None) -> set[str]:
    s = trade_assets_for_team(session, team_id, raw_dir=raw_dir)
    return {str(x["drag_key"]) for x in [*s.get("roster", []), *s.get("unsigned", [])]}


def _manual_pick_key_ok(key: str, prefix: str, cap: int) -> bool:
    parts = key.split(":")
    if len(parts) < 3:
        return False
    if parts[0] != prefix:
        return False
    try:
        rnd = int(parts[1])
    except ValueError:
        return False
    if rnd < 1 or rnd > cap:
        return False
    slug = ":".join(parts[2:])
    if not slug or not re.match(r"^[A-Za-z0-9_-]{4,64}$", slug):
        return False
    return True


def _legacy_db_pick_owned(session: Session, team_id: int, key: str) -> bool:
    if not key.startswith("pick:"):
        return False
    try:
        pkid = int(key.split(":", 1)[1])
    except (ValueError, IndexError):
        return False
    pk = session.get(DraftPick, pkid)
    return bool(
        pk
        and pk.team_id == team_id
        and pk.player_id is None
    )


def _key_valid_leaving_from_team(
    session: Session,
    from_team_id: int,
    key: str,
    *,
    raw_dir: Path | None,
    draft_cap: int,
) -> bool:
    if _manual_pick_key_ok(key, MANUAL_PICK_PREFIX_LEFT, draft_cap):
        return True
    if _legacy_db_pick_owned(session, from_team_id, key):
        return True
    return key in player_drag_keys_for_team(session, from_team_id, raw_dir)


def _key_valid_leaving_to_team(
    session: Session,
    to_team_id: int,
    key: str,
    *,
    raw_dir: Path | None,
    draft_cap: int,
) -> bool:
    if _manual_pick_key_ok(key, MANUAL_PICK_PREFIX_RIGHT, draft_cap):
        return True
    if _legacy_db_pick_owned(session, to_team_id, key):
        return True
    return key in player_drag_keys_for_team(session, to_team_id, raw_dir)


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
    session: Session,
    from_team_id: int,
    to_team_id: int,
    left_out: list[str],
    right_out: list[str],
    *,
    raw_dir: Path | None,
    league_slug: str,
    draft_round_cap: int | None = None,
) -> str | None:
    if len(left_out) > MAX_ASSETS_PER_SIDE or len(right_out) > MAX_ASSETS_PER_SIDE:
        return f"Each side may include at most {MAX_ASSETS_PER_SIDE} assets."
    cap = (
        int(draft_round_cap)
        if draft_round_cap is not None
        else trade_tool_draft_round_cap(session, league_slug)
    )
    for k in left_out:
        if not _key_valid_leaving_from_team(session, from_team_id, k, raw_dir=raw_dir, draft_cap=cap):
            return "One or more assets leaving your team are not valid for your roster."
    for k in right_out:
        if not _key_valid_leaving_to_team(session, to_team_id, k, raw_dir=raw_dir, draft_cap=cap):
            return "One or more assets from the partner team are not valid."
    if not left_out and not right_out:
        return "Add at least one asset to one side of the ledger."
    return None


def describe_drag_key(session: Session, drag_key: str) -> str:
    if drag_key.startswith(f"{MANUAL_PICK_PREFIX_LEFT}:") or drag_key.startswith(
        f"{MANUAL_PICK_PREFIX_RIGHT}:"
    ):
        parts = drag_key.split(":")
        if len(parts) >= 2:
            try:
                rnd = int(parts[1])
                return f"Draft pick (round {rnd})"
            except ValueError:
                pass
        return "Draft pick (manual)"
    if ":" not in drag_key:
        return drag_key
    kind, _, rest = drag_key.partition(":")
    if kind == "player":
        try:
            eid = int(rest)
        except ValueError:
            return drag_key
        pl = session.get(Player, eid)
        if pl:
            pos = (pl.position or "").strip() or "—"
            return f"{pos} {pl.full_name}".strip()
        return f"Player #{eid}"
    if kind == "pick":
        try:
            eid = int(rest)
        except ValueError:
            return drag_key
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
