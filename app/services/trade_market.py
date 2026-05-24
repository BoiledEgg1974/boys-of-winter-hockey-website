"""GM Trade Market: selling listings, buying needs, Discord payloads."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, PlayerContract, Team
from app.services.discord_events import build_league_public_url, enqueue_discord_event
from app.services.draft_pick_ownership import (
    describe_draft_pick_row,
    draft_pick_asset_dicts,
    draft_pick_drag_key,
    draft_pick_owned_by_team,
    owned_draft_pick_drag_keys,
    parse_draft_pick_drag_key,
)
from app.services.gm_messaging import gm_display_name
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row
from app.services.trade_tool import enrich_trade_player_row, trade_assets_for_team
from app.site_models import MemberWatchlistItem, TradeMarketBuyingNeed, TradeMarketListing, User

TRADE_MARKET_SELLING_EVENT = "trade_market_selling_posted"
TRADE_MARKET_BUYING_EVENT = "trade_market_buying_posted"

BUYING_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("draft_picks", "Draft Picks"),
    ("prospects", "Prospects"),
    ("top_4_defense", "Top 4 Defense"),
    ("top_6_forwards", "Top 6 Forwards"),
    ("defensive_forwards", "Defensive Forwards"),
    ("defensive_defensemen", "Defensive Defensemen"),
    ("goalie", "Goalie"),
    ("sniper_playmaker", "Sniper/Playmaker"),
)

BUYING_CATEGORY_KEYS = frozenset(k for k, _ in BUYING_CATEGORIES)


def buying_category_label(key: str) -> str:
    for k, label in BUYING_CATEGORIES:
        if k == key:
            return label
    return str(key or "").replace("_", " ").title()


def _player_asset_ref(player_id: int, section: str) -> str:
    return f"player:{int(player_id)}:{section}"


def parse_player_asset_ref(ref: str) -> tuple[int, str] | None:
    parts = str(ref or "").split(":")
    if len(parts) < 3 or parts[0] != "player":
        return None
    try:
        return int(parts[1]), str(parts[2])
    except ValueError:
        return None


def _agi_from_ratings(pl: Player) -> float | None:
    rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None))
    if not rr:
        return None
    return fhm_abi_pot_float(rr.get("agility"))


def selectable_selling_assets(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    team_id: int,
    raw_dir,
) -> dict[str, list[dict[str, Any]]]:
    base = trade_assets_for_team(
        league_session,
        int(team_id),
        raw_dir=raw_dir,
        league_slug=league_slug,
    )
    for row in base.get("roster", []):
        pid = int(row["id"])
        row["asset_ref"] = _player_asset_ref(pid, "roster")
        row["asset_type"] = "contract"
    for row in base.get("unsigned", []):
        pid = int(row["id"])
        sec = str(row.get("section") or "unsigned")
        row["asset_ref"] = _player_asset_ref(pid, sec)
        row["asset_type"] = "prospect" if sec == "unsigned" else "rights"
    for row in base.get("draft_picks", []):
        row["asset_ref"] = draft_pick_drag_key(int(row["id"]))
        row["asset_type"] = "draft_pick"
    return base


def _validate_owned_asset(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    team_id: int,
    asset_type: str,
    asset_ref: str,
    raw_dir,
) -> bool:
    at = str(asset_type or "").strip().lower()
    ref = str(asset_ref or "").strip()
    if at == "draft_pick":
        return (
            draft_pick_owned_by_team(
                site_session,
                league_slug=league_slug,
                team_id=int(team_id),
                drag_key=ref,
            )
            is not None
        )
    allowed = selectable_selling_assets(
        site_session,
        league_session,
        league_slug=league_slug,
        team_id=int(team_id),
        raw_dir=raw_dir,
    )
    for bucket in ("roster", "unsigned", "draft_picks"):
        for row in allowed.get(bucket, []):
            if str(row.get("asset_ref")) == ref and str(row.get("asset_type")) == at:
                return True
    return False


def replace_selling_listings(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    user_id: int,
    team_id: int,
    items: list[dict[str, Any]],
    raw_dir,
) -> tuple[list[TradeMarketListing], str | None]:
    """Replace active listings for team. Each item: asset_type, asset_ref, asking_price, wants, note."""
    slug = str(league_slug or "").strip()
    site_session.execute(
        delete(TradeMarketListing).where(
            TradeMarketListing.league_slug == slug,
            TradeMarketListing.team_id == int(team_id),
        )
    )
    rows: list[TradeMarketListing] = []
    for it in items:
        at = str(it.get("asset_type") or "").strip().lower()
        ref = str(it.get("asset_ref") or "").strip()
        if not at or not ref:
            continue
        if not _validate_owned_asset(
            site_session,
            league_session,
            league_slug=slug,
            team_id=int(team_id),
            asset_type=at,
            asset_ref=ref,
            raw_dir=raw_dir,
        ):
            return [], "One or more selected assets are not on your team."
        wants = it.get("wants") or []
        if isinstance(wants, str):
            wants = [wants]
        wants_clean = [str(w).strip() for w in wants if str(w).strip() in BUYING_CATEGORY_KEYS]
        row = TradeMarketListing(
            league_slug=slug,
            user_id=int(user_id),
            team_id=int(team_id),
            asset_type=at,
            asset_ref=ref,
            asking_price=str(it.get("asking_price") or "").strip()[:120],
            wants_json=json.dumps(wants_clean),
            note=str(it.get("note") or "").strip()[:2000],
            status="active",
        )
        site_session.add(row)
        rows.append(row)
    site_session.flush()
    _enqueue_trade_market_watch_alerts(
        site_session,
        league_slug=slug,
        actor_user_id=int(user_id),
        team_id=int(team_id),
        event_key="trade_market_watch_selling",
        title="Trade Market watch alert",
        body="A team on your watchlist updated its Trade Market selling list.",
        source_prefix="selling",
        source_ids=[int(r.id) for r in rows],
    )
    return rows, None


def replace_buying_needs(
    site_session: Session,
    *,
    league_slug: str,
    user_id: int,
    team_id: int,
    categories: list[str],
    note: str = "",
) -> list[TradeMarketBuyingNeed]:
    slug = str(league_slug or "").strip()
    site_session.execute(
        delete(TradeMarketBuyingNeed).where(
            TradeMarketBuyingNeed.league_slug == slug,
            TradeMarketBuyingNeed.team_id == int(team_id),
        )
    )
    rows: list[TradeMarketBuyingNeed] = []
    seen: set[str] = set()
    for raw in categories:
        cat = str(raw or "").strip()
        if cat not in BUYING_CATEGORY_KEYS or cat in seen:
            continue
        seen.add(cat)
        row = TradeMarketBuyingNeed(
            league_slug=slug,
            user_id=int(user_id),
            team_id=int(team_id),
            category=cat,
            note=str(note or "").strip()[:2000],
            status="active",
        )
        site_session.add(row)
        rows.append(row)
    site_session.flush()
    _enqueue_trade_market_watch_alerts(
        site_session,
        league_slug=slug,
        actor_user_id=int(user_id),
        team_id=int(team_id),
        event_key="trade_market_watch_buying",
        title="Trade Market watch alert",
        body="A team on your watchlist updated its Trade Market buying interests.",
        source_prefix="buying",
        source_ids=[int(r.id) for r in rows if r.id is not None],
    )
    return rows


def _enqueue_trade_market_watch_alerts(
    site_session: Session,
    *,
    league_slug: str,
    actor_user_id: int,
    team_id: int,
    event_key: str,
    title: str,
    body: str,
    source_prefix: str,
    source_ids: list[int],
) -> None:
    if not source_ids:
        return
    watchers = site_session.scalars(
        select(MemberWatchlistItem.user_id).where(
            MemberWatchlistItem.league_slug == league_slug,
            MemberWatchlistItem.target_type == "team",
            MemberWatchlistItem.target_ref == str(int(team_id)),
            MemberWatchlistItem.user_id != int(actor_user_id),
        )
    ).all()
    seen: set[int] = set()
    try:
        from app.services.discord_direct_messages import enqueue_direct_message

        for uid in watchers:
            uid_i = int(uid)
            if uid_i in seen:
                continue
            seen.add(uid_i)
            enqueue_direct_message(
                site_session,
                league_slug=league_slug,
                recipient_user_id=uid_i,
                event_key=event_key,
                title=title,
                body=body,
                source_type="trade_market_watch",
                source_id=f"{source_prefix}:{int(team_id)}:{','.join(str(x) for x in source_ids[:8])}",
                url=build_league_public_url(league_slug, "/trade-market"),
            )
    except Exception:
        pass


def enrich_listing_row(
    site_session: Session,
    league_session: Session,
    listing: TradeMarketListing,
    *,
    teams_by_id: dict[int, Team],
    users_by_id: dict[int, User],
) -> dict[str, Any]:
    from app.site_models import TradeMarketDraftPickOwnership

    tm = teams_by_id.get(int(listing.team_id))
    u = users_by_id.get(int(listing.user_id))
    gm = gm_display_name(u) if u else f"User #{listing.user_id}"
    at = str(listing.asset_type or "")
    ref = str(listing.asset_ref or "")
    wants = []
    try:
        wants = json.loads(listing.wants_json or "[]")
    except json.JSONDecodeError:
        wants = []
    wants_labels = [buying_category_label(str(w)) for w in wants if w]

    out: dict[str, Any] = {
        "listing_id": int(listing.id),
        "team_id": int(listing.team_id),
        "user_id": int(listing.user_id),
        "asset_ref": ref,
        "team_name": tm.full_display_name() if tm else f"Team {listing.team_id}",
        "gm_name": gm,
        "asset_type": at,
        "asset_type_label": {
            "contract": "Contract",
            "prospect": "Prospect",
            "rights": "Rights",
            "draft_pick": "Draft pick",
        }.get(at, at),
        "asset_label": ref,
        "asking_price": str(listing.asking_price or ""),
        "wants": wants,
        "wants_labels": ", ".join(wants_labels) if wants_labels else "—",
        "note": str(listing.note or ""),
        "updated_at": listing.updated_at,
        "ovr": None,
        "abi": None,
        "agi": None,
        "aav": None,
        "player_id": None,
        "positions": "",
    }

    if at == "draft_pick":
        rid = parse_draft_pick_drag_key(ref)
        if rid:
            dp = site_session.get(TradeMarketDraftPickOwnership, int(rid))
            if dp:
                orig = league_session.get(Team, int(dp.original_team_id)) if dp.original_team_id else None
                owner = league_session.get(Team, int(dp.owner_team_id)) if dp.owner_team_id else None
                out["asset_label"] = describe_draft_pick_row(
                    dp, original_team=orig, owner_team=owner
                )
        return out

    parsed = parse_player_asset_ref(ref)
    if parsed:
        pid, _sec = parsed
        pl = league_session.get(Player, int(pid))
        if pl:
            tmp: dict[str, Any] = {}
            enrich_trade_player_row(league_session, pl, tmp)
            out["asset_label"] = pl.full_name or out["asset_label"]
            out["player_id"] = int(pl.id)
            out["positions"] = tmp.get("positions") or (pl.position or "")
            out["ovr"] = tmp.get("ovr")
            out["abi"] = float(pl.overall_ability) if pl.overall_ability is not None else None
            out["agi"] = _agi_from_ratings(pl)
            out["abi_style"] = tmp.get("abi_style", "")
            out["headshot_rel"] = tmp.get("headshot_rel")
            pc = league_session.scalar(
                select(PlayerContract).where(PlayerContract.player_id == int(pl.id)).limit(1)
            )
            if pc and pc.average_salary is not None:
                out["aav"] = int(pc.average_salary)
    return out


def active_selling_rows(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> list[dict[str, Any]]:
    slug = str(league_slug or "").strip()
    listings = list(
        site_session.scalars(
            select(TradeMarketListing)
            .where(
                TradeMarketListing.league_slug == slug,
                TradeMarketListing.status == "active",
            )
            .order_by(TradeMarketListing.updated_at.desc())
        ).all()
    )
    if not listings:
        return []
    team_ids = {int(x.team_id) for x in listings}
    user_ids = {int(x.user_id) for x in listings}
    teams = {
        t.id: t
        for t in league_session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    }
    users = {
        u.id: u for u in site_session.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    return [
        enrich_listing_row(
            site_session, league_session, lst, teams_by_id=teams, users_by_id=users
        )
        for lst in listings
    ]


def sort_selling_rows(
    rows: list[dict[str, Any]],
    *,
    sort_key: str = "updated",
    order: str = "desc",
) -> list[dict[str, Any]]:
    key = (sort_key or "updated").strip().lower()
    rev = str(order or "desc").strip().lower() != "asc"

    def sort_val(row: dict[str, Any]):
        if key == "team":
            return str(row.get("team_name") or "").lower()
        if key in ("player", "asset"):
            return str(row.get("asset_label") or "").lower()
        if key == "type":
            return str(row.get("asset_type_label") or "").lower()
        if key == "ovr":
            v = row.get("ovr")
            return (v if v is not None else -1, str(row.get("asset_label") or ""))
        if key == "abi":
            v = row.get("abi")
            return (v if v is not None else -1, str(row.get("asset_label") or ""))
        if key == "agi":
            v = row.get("agi")
            return (v if v is not None else -1, str(row.get("asset_label") or ""))
        if key == "aav":
            v = row.get("aav")
            return (v if v is not None else -1, str(row.get("asset_label") or ""))
        if key in ("ask", "asking"):
            return str(row.get("asking_price") or "").lower()
        if key == "wants":
            return str(row.get("wants_labels") or "").lower()
        if key == "updated":
            return row.get("updated_at") or datetime.min
        return str(row.get("asset_label") or "").lower()

    return sorted(rows, key=sort_val, reverse=rev)


def active_buying_rows(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> list[dict[str, Any]]:
    slug = str(league_slug or "").strip()
    needs = list(
        site_session.scalars(
            select(TradeMarketBuyingNeed)
            .where(
                TradeMarketBuyingNeed.league_slug == slug,
                TradeMarketBuyingNeed.status == "active",
            )
            .order_by(TradeMarketBuyingNeed.updated_at.desc())
        ).all()
    )
    if not needs:
        return []
    by_team: dict[int, dict[str, Any]] = {}
    team_ids = {int(n.team_id) for n in needs}
    user_ids = {int(n.user_id) for n in needs}
    teams = {
        t.id: t
        for t in league_session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    }
    users = {
        u.id: u for u in site_session.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    for n in needs:
        tid = int(n.team_id)
        if tid not in by_team:
            tm = teams.get(tid)
            u = users.get(int(n.user_id))
            by_team[tid] = {
                "team_id": tid,
                "user_id": int(n.user_id),
                "team_name": tm.full_display_name() if tm else f"Team {tid}",
                "gm_name": gm_display_name(u) if u else f"User #{n.user_id}",
                "categories": [],
                "category_labels": [],
                "note": str(n.note or ""),
                "updated_at": n.updated_at,
            }
        by_team[tid]["categories"].append(str(n.category))
        by_team[tid]["category_labels"].append(buying_category_label(str(n.category)))
        if n.updated_at and (
            not by_team[tid]["updated_at"] or n.updated_at > by_team[tid]["updated_at"]
        ):
            by_team[tid]["updated_at"] = n.updated_at
    out = list(by_team.values())
    for row in out:
        row["category_labels"] = ", ".join(row["category_labels"])
    return out


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def selling_discord_body(
    site_session: Session,
    league_session: Session,
    listings: list[TradeMarketListing],
    *,
    league_slug: str,
    teams_by_id: dict[int, Team] | None = None,
    users_by_id: dict[int, User] | None = None,
) -> str:
    if not listings:
        return "Selling list cleared (no assets listed)."
    lines = []
    for lst in listings:
        enriched = enrich_listing_row(
            site_session,
            league_session,
            lst,
            teams_by_id=teams_by_id or {},
            users_by_id=users_by_id or {},
        )
        ask = enriched.get("asking_price") or "—"
        wants = enriched.get("wants_labels") or "—"
        asset_label = str(enriched.get("asset_label", lst.asset_ref) or lst.asset_ref)
        player_id = enriched.get("player_id")
        if player_id:
            player_url = build_league_public_url(league_slug, f"/player/{int(player_id)}")
            if player_url:
                asset_label = f"[{asset_label}]({player_url})"
        lines.append(
            f"• {asset_label} — ask {ask} · wants {wants}"
        )
    return "Now selling:\n" + "\n".join(lines)


def maybe_enqueue_selling_discord(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    team_id: int,
    listings: list[TradeMarketListing],
    team_fields: dict | None = None,
    previous_hash: str = "",
) -> None:
    body = selling_discord_body(
        site_session, league_session, listings, league_slug=league_slug
    )
    h = _content_hash(body)
    old_hash = str(previous_hash or "").strip()
    if h == old_hash:
        return
    url = build_league_public_url(league_slug, "/trade-market") or f"/{league_slug}/trade-market"
    payload = {
        "title": "Trade Market — selling update",
        "body": body,
        "body_preview": body[:280],
        "url": url,
        "team_id": int(team_id),
        **(team_fields or {}),
    }
    enqueue_discord_event(
        site_session,
        league_slug=league_slug,
        event_key=TRADE_MARKET_SELLING_EVENT,
        payload=payload,
        created_by_user_id=int(listings[0].user_id) if listings else None,
        source_type="trade_market_selling",
        source_id=f"{league_slug}:{team_id}:{h}",
    )
    for lst in listings:
        lst.discord_payload_hash = h


def maybe_enqueue_buying_discord(
    site_session: Session,
    *,
    league_slug: str,
    team_id: int,
    needs: list[TradeMarketBuyingNeed],
    team_fields: dict | None = None,
    previous_hash: str = "",
) -> None:
    cats = [buying_category_label(str(n.category)) for n in needs]
    if not cats:
        body = "Buying wishlist cleared."
    else:
        body = "Looking to acquire:\n" + "\n".join(f"• {c}" for c in cats)
    h = _content_hash(body)
    old_hash = str(previous_hash or "").strip()
    if h == old_hash:
        return
    url = build_league_public_url(league_slug, "/trade-market") or f"/{league_slug}/trade-market"
    payload = {
        "title": "Trade Market — buying interests",
        "body": body,
        "body_preview": body[:280],
        "url": url,
        "team_id": int(team_id),
        **(team_fields or {}),
    }
    enqueue_discord_event(
        site_session,
        league_slug=league_slug,
        event_key=TRADE_MARKET_BUYING_EVENT,
        payload=payload,
        created_by_user_id=int(needs[0].user_id) if needs else None,
        source_type="trade_market_buying",
        source_id=f"{league_slug}:{team_id}:{h}",
    )
    for n in needs:
        n.discord_payload_hash = h
