from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

import os

from flask import current_app, has_app_context
from sqlalchemy import delete, or_, select, update

from app.site_models import (
    DiscordBotHeartbeat,
    DiscordChannelRoute,
    DiscordDeliveredSource,
    DiscordLeagueBotConfig,
    DiscordOutboundEvent,
    GmApprovalRequest,
    LeagueDraft,
    LeagueDraftPick,
    LeagueExpansionDraft,
    LeagueExpansionDraftPick,
    NewsArticle,
    StaffChangeRequest,
)

NEWS_DISCORD_EVENT_KEYS = frozenset(
    {
        "news_published",
        "gm_news_published",
        "admin_news_published",
        "story_published",
    }
)

OPS_TEXT_ONLY_DISCORD_EVENT_KEYS = frozenset(
    {
        "trade_request",
        "staff_transaction_posted",
        "draft_hub_pick_made",
        "expansion_draft_pick_made",
        "bowl_six_leaders_update",
    }
)

BOWL_SIX_LEADERS_EVENT_KEY = "bowl_six_leaders_update"

REPEATABLE_DISCORD_EVENT_KEYS = frozenset({BOWL_SIX_LEADERS_EVENT_KEY})

EVENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DISCORD_SNOWFLAKE_PATTERN = re.compile(r"^\d{17,20}$")

# Default routes seeded per league (blank discord_channel_id until admin fills them in).
DEFAULT_EVENT_KEYS = {
    "news_published",
    "gm_news_published",
    "admin_news_published",
    "ap_redemption_posted",
    "trade_request",
    "announcement_posted",
    "draft_hub_pick_made",
    "expansion_draft_pick_made",
    "staff_transaction_posted",
    "bowl_six_leaders_update",
}

DEFAULT_EVENT_CHANNEL_KEY = {
    "news_published": "league-news",
    "gm_news_published": "team-news",
    "admin_news_published": "league-news",
    "ap_redemption_posted": "ap-redemptions",
    "trade_request": "transactions",
    "announcement_posted": "league-announcements",
    "draft_hub_pick_made": "draft-discussion",
    "expansion_draft_pick_made": "expansion-draft-discussion",
    "staff_transaction_posted": "staff-hirings-firings",
    "bowl_six_leaders_update": "bowl-six-leaders",
}

DEFAULT_EVENT_LABELS = {
    "news_published": "News (legacy; use gm/admin keys)",
    "gm_news_published": "Team news — GM submissions (moderated)",
    "admin_news_published": "League news — admin compose",
    "ap_redemption_posted": "AP redemption approved",
    "trade_request": "Trade / ops request",
    "announcement_posted": "Commissioner announcement",
    "draft_hub_pick_made": "Draft Hub pick (live)",
    "expansion_draft_pick_made": "Expansion draft pick (live)",
    "staff_transaction_posted": "Staff hire / fire approved",
    "bowl_six_leaders_update": "BOWL Six live leaders (post + edit)",
}

MAX_DELIVERY_ATTEMPTS = 3


def _parse_suppressed_default_route_keys(raw: object) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return set()
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return set()
        if isinstance(data, list):
            return {str(x).strip() for x in data if str(x).strip()}
        return set()
    return set()


def _suppressed_default_route_keys(session, league_slug: str) -> set[str]:
    row = session.scalar(
        select(DiscordLeagueBotConfig).where(DiscordLeagueBotConfig.league_slug == league_slug).limit(1)
    )
    if row is None:
        return set()
    return _parse_suppressed_default_route_keys(getattr(row, "suppressed_default_route_keys_json", ""))


def _ensure_discord_bot_cfg_row(session, league_slug: str) -> DiscordLeagueBotConfig:
    row = session.scalar(
        select(DiscordLeagueBotConfig).where(DiscordLeagueBotConfig.league_slug == league_slug).limit(1)
    )
    if row is None:
        row = DiscordLeagueBotConfig(
            league_slug=league_slug,
            guild_id="",
            is_enabled=True,
            notes="",
            suppressed_default_route_keys_json="[]",
            updated_by_user_id=None,
            updated_at=datetime.utcnow(),
        )
        session.add(row)
        session.flush()
    return row


def _remember_removed_default_route(session, league_slug: str, event_key: str) -> None:
    key = str(event_key or "").strip()
    if key not in DEFAULT_EVENT_KEYS:
        return
    cfg = _ensure_discord_bot_cfg_row(session, league_slug)
    suppressed = _parse_suppressed_default_route_keys(cfg.suppressed_default_route_keys_json)
    suppressed.add(key)
    cfg.suppressed_default_route_keys_json = json.dumps(sorted(suppressed))


def _forget_removed_default_route(session, league_slug: str, event_key: str) -> None:
    key = str(event_key or "").strip()
    row = session.scalar(
        select(DiscordLeagueBotConfig).where(DiscordLeagueBotConfig.league_slug == league_slug).limit(1)
    )
    if row is None:
        return
    suppressed = _parse_suppressed_default_route_keys(row.suppressed_default_route_keys_json)
    if key not in suppressed:
        return
    suppressed.discard(key)
    row.suppressed_default_route_keys_json = json.dumps(sorted(suppressed)) if suppressed else "[]"


def is_valid_event_key(key: str) -> bool:
    return bool(EVENT_KEY_PATTERN.match(str(key or "").strip()))


def is_valid_discord_channel_id(channel_id: str) -> bool:
    cid = str(channel_id or "").strip()
    return not cid or bool(DISCORD_SNOWFLAKE_PATTERN.match(cid))


def league_mount_path(league_slug: str) -> str:
    slug = str(league_slug or "").strip().strip("/")
    return f"/{slug}" if slug else ""


def team_fields_for_discord(team) -> dict:
    """Build payload fields for Discord formatters (FHM team id + abbrev for emoji maps)."""
    if team is None:
        return {}
    out: dict = {}
    name_fn = getattr(team, "full_display_name", None)
    if callable(name_fn):
        out["team_name"] = str(name_fn() or "")
    else:
        out["team_name"] = str(getattr(team, "name", "") or "")
    abbr = str(getattr(team, "abbreviation", "") or "").strip()
    if abbr:
        out["team_abbrev"] = abbr
    fhm = getattr(team, "fhm_team_id", None)
    if fhm is not None and str(fhm).strip():
        try:
            out["fhm_team_id"] = int(str(fhm).strip())
        except ValueError:
            out["fhm_team_id"] = str(fhm).strip()
    return out


def resolve_site_public_base_url() -> str:
    """Public site origin (no trailing slash), from Flask config or ``SITE_PUBLIC_BASE_URL`` env."""
    base = ""
    try:
        base = str(current_app.config.get("SITE_PUBLIC_BASE_URL") or "").rstrip("/")
    except RuntimeError:
        base = ""
    if not base:
        base = str(os.environ.get("SITE_PUBLIC_BASE_URL") or "").rstrip("/")
    return base


def build_league_public_url(league_slug: str, path: str = "/") -> str:
    """Absolute https URL for Discord embeds and outbound links.

    Returns empty string when ``SITE_PUBLIC_BASE_URL`` is unset (never a relative path).
    """
    base = resolve_site_public_base_url()
    if not base:
        return ""
    mount = league_mount_path(league_slug)
    rel = str(path or "/")
    if not rel.startswith("/"):
        rel = f"/{rel}"
    return f"{base}{mount}{rel}"


def build_news_article_public_url(league_slug: str, article_id: int | str) -> str:
    """Public Around the League article (``/league-headlines#a<id>``)."""
    try:
        aid = int(article_id)
    except (TypeError, ValueError):
        return ""
    if aid <= 0:
        return ""
    return build_league_public_url(league_slug, f"/league-headlines#a{aid}")


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def trade_request_discord_body(row: GmApprovalRequest) -> str:
    """Human-readable ops / trade request text for Discord."""
    lines: list[str] = []
    title = str(row.title or "").strip()
    if title:
        lines.append(title)
    req_type = str(row.request_type or "").strip()
    if req_type and req_type != "trade":
        plain = str(row.body or "").strip()
        if plain:
            lines.append(plain)
        return "\n".join(lines)
    payload = _parse_json_object(row.body or "")
    details = str(payload.get("details") or "").strip()
    if details:
        lines.append(details)
    inc = payload.get("incoming_count")
    out = payload.get("outgoing_count")
    if inc is not None and out is not None:
        lines.append(f"Incoming skaters: {inc} · Outgoing skaters: {out}")
    partner_tid = payload.get("partner_team_id")
    partner_inc = payload.get("partner_incoming_count")
    partner_out = payload.get("partner_outgoing_count")
    if partner_tid is not None and partner_inc is not None and partner_out is not None:
        lines.append(
            f"Partner team #{partner_tid}: +{partner_inc} / -{partner_out}"
        )
    if not lines:
        plain = str(row.body or "").strip()
        if plain and not plain.startswith("{"):
            lines.append(plain)
    return "\n".join(lines)


def trade_request_discord_payload(
    row: GmApprovalRequest,
    *,
    team_fields: dict | None = None,
    **extra: object,
) -> dict:
    body = trade_request_discord_body(row)
    title = str(row.title or "").strip() or f"Trade / ops request #{int(row.id)}"
    return {
        "request_id": int(row.id),
        "request_type": str(row.request_type or ""),
        "team_id": int(row.team_id),
        "status": str(row.status or ""),
        "admin_note": str(row.admin_note or ""),
        "title": title,
        "body": body,
        "body_preview": body[:280],
        "has_image": False,
        **(team_fields or {}),
        **extra,
    }


def staff_transaction_discord_payload(
    req: StaffChangeRequest,
    *,
    role_label: str = "",
    team_fields: dict | None = None,
    gm_email: str = "",
    **extra: object,
) -> dict:
    action = "hired" if str(req.request_type or "") == "hire" else "fired"
    staff_name = str(req.staff_name or "").strip()
    body_lines = [f"{staff_name} ({role_label})" if role_label else staff_name]
    if gm_email:
        body_lines.append(f"GM: {gm_email}")
    body = "\n".join([ln for ln in body_lines if ln])
    title = "Staff hired" if action == "hired" else "Staff fired"
    return {
        "request_id": int(req.id),
        "action": action,
        "staff_name": staff_name,
        "role_label": role_label,
        "gm_email": gm_email,
        "title": title,
        "body": body,
        "body_preview": body[:280],
        "has_image": False,
        **(team_fields or {}),
        **extra,
    }


def draft_hub_pick_discord_payload(
    *,
    draft: LeagueDraft,
    pick: LeagueDraftPick,
    player_name: str,
    player_pos: str = "",
    team_fields: dict | None = None,
    **extra: object,
) -> dict:
    pos = str(player_pos or "").strip()
    ply = player_name + (f" ({pos})" if pos else "")
    pick_line = (
        f"Round {int(pick.round)} · Overall #{int(pick.overall_pick)} · {ply}"
        f" · {str(pick.source or '')}"
    )
    dname = str(draft.name or "Draft Hub")
    return {
        "title": dname,
        "draft_id": int(draft.id),
        "draft_name": dname,
        "overall_pick": int(pick.overall_pick),
        "round": int(pick.round),
        "pick_source": str(pick.source or ""),
        "player_name": player_name,
        "player_pos": pos,
        "body": pick_line,
        "body_preview": pick_line[:280],
        "has_image": False,
        **(team_fields or {}),
        **extra,
    }


def expansion_draft_pick_discord_payload(
    *,
    draft: LeagueExpansionDraft,
    pick: LeagueExpansionDraftPick,
    player_name: str,
    team_fields: dict | None = None,
    **extra: object,
) -> dict:
    phase = str(pick.phase or "").strip()
    ph = f"[{phase}] " if phase else ""
    pick_line = (
        f"{ph}Round {int(pick.round)} · Overall #{int(pick.overall_pick)} · {player_name}"
        f" · {str(pick.source or '')}"
    )
    dname = str(draft.name or "Expansion draft")
    return {
        "title": dname,
        "draft_id": int(draft.id),
        "draft_name": dname,
        "overall_pick": int(pick.overall_pick),
        "round": int(pick.round),
        "phase": phase,
        "pick_source": str(pick.source or ""),
        "player_name": player_name,
        "body": pick_line,
        "body_preview": pick_line[:280],
        "has_image": False,
        **(team_fields or {}),
        **extra,
    }


def news_article_discord_payload(article: NewsArticle, **extra: object) -> dict:
    """Queue payload fields for news-style Discord events."""
    body = str(article.body or "")
    has_image = bool(str(article.image_rel_path or "").strip())
    return {
        "article_id": int(article.id),
        "title": str(article.title or ""),
        "body": body,
        "body_preview": body[:280],
        "has_image": has_image,
        **extra,
    }


def enrich_discord_payload_for_bot(
    session,
    *,
    league_slug: str,
    event_key: str,
    payload: dict,
) -> dict:
    """Fill full article body and image flag for pending delivery (replay-safe)."""
    out = dict(payload or {})
    ek = str(event_key or "")
    if ek in NEWS_DISCORD_EVENT_KEYS:
        aid = out.get("article_id")
        if aid is None:
            return out
        try:
            article_id = int(aid)
        except (TypeError, ValueError):
            return out
        art = session.get(NewsArticle, article_id)
        if art is None or str(art.league_slug or "") != str(league_slug or ""):
            return out
        enriched = news_article_discord_payload(art)
        merged = {**enriched, **out}
        merged["body"] = enriched["body"]
        merged["has_image"] = enriched["has_image"]
        if len(str(out.get("body_preview") or "")) < len(enriched["body_preview"]):
            merged["body_preview"] = enriched["body_preview"]
        return merged
    if ek == "announcement_posted":
        if not str(out.get("body") or "").strip():
            preview = str(out.get("body_preview") or "").strip()
            if preview:
                out["body"] = preview
        out.setdefault("has_image", False)
        return out
    if ek == "trade_request":
        rid = out.get("request_id")
        if rid is None:
            return out
        try:
            request_id = int(rid)
        except (TypeError, ValueError):
            return out
        row = session.get(GmApprovalRequest, request_id)
        if row is None or str(row.league_slug or "") != str(league_slug or ""):
            return out
        enriched = trade_request_discord_payload(row, team_fields={})
        merged = {**enriched, **out}
        merged["body"] = enriched["body"]
        merged["has_image"] = False
        if str(out.get("admin_note") or "").strip():
            note = str(out["admin_note"]).strip()
            if note not in merged["body"]:
                merged["body"] = f"{merged['body']}\n\nAdmin note: {note}".strip()
        return merged
    if ek == "staff_transaction_posted":
        rid = out.get("request_id")
        if rid is None:
            return out
        try:
            request_id = int(rid)
        except (TypeError, ValueError):
            return out
        req = session.get(StaffChangeRequest, request_id)
        if req is None or str(req.league_slug or "") != str(league_slug or ""):
            return out
        enriched = staff_transaction_discord_payload(
            req,
            role_label=str(out.get("role_label") or ""),
            team_fields={},
            gm_email=str(out.get("gm_email") or ""),
        )
        merged = {**enriched, **out}
        merged["body"] = enriched["body"]
        merged["has_image"] = False
        return merged
    if ek == "draft_hub_pick_made":
        pick_id = out.get("pick_id") or out.get("source_id")
        if pick_id is None:
            return _fill_body_from_preview(out)
        try:
            pid = int(pick_id)
        except (TypeError, ValueError):
            return _fill_body_from_preview(out)
        pk = session.get(LeagueDraftPick, pid)
        if pk is None:
            return _fill_body_from_preview(out)
        draft = session.get(LeagueDraft, int(pk.league_draft_id))
        if draft is None or str(draft.league_slug or "") != str(league_slug or ""):
            return _fill_body_from_preview(out)
        enriched = draft_hub_pick_discord_payload(
            draft=draft,
            pick=pk,
            player_name=str(out.get("player_name") or ""),
            player_pos=str(out.get("player_pos") or ""),
            team_fields={},
        )
        merged = {**enriched, **out}
        merged["body"] = enriched["body"]
        merged["has_image"] = False
        return merged
    if ek == "expansion_draft_pick_made":
        pick_id = out.get("pick_id") or out.get("source_id")
        if pick_id is None:
            return _fill_body_from_preview(out)
        try:
            pid = int(pick_id)
        except (TypeError, ValueError):
            return _fill_body_from_preview(out)
        pk = session.get(LeagueExpansionDraftPick, pid)
        if pk is None:
            return _fill_body_from_preview(out)
        draft = session.get(LeagueExpansionDraft, int(pk.league_expansion_draft_id))
        if draft is None or str(draft.league_slug or "") != str(league_slug or ""):
            return _fill_body_from_preview(out)
        enriched = expansion_draft_pick_discord_payload(
            draft=draft,
            pick=pk,
            player_name=str(out.get("player_name") or ""),
            team_fields={},
        )
        merged = {**enriched, **out}
        merged["body"] = enriched["body"]
        merged["has_image"] = False
        return merged
    return out


def _fill_body_from_preview(payload: dict) -> dict:
    out = dict(payload or {})
    if not str(out.get("body") or "").strip():
        preview = str(out.get("body_preview") or "").strip()
        if preview:
            out["body"] = preview
    out.setdefault("has_image", False)
    return out


def normalize_discord_payload_url(league_slug: str, url: str) -> str:
    """Fix queued relative URLs (e.g. ``/bowl-historical/``) for Discord embeds."""
    u = str(url or "").strip()
    if not u:
        return ""
    if u.lower().startswith(("http://", "https://")):
        return u
    base = resolve_site_public_base_url()
    if not base:
        return ""
    mount = league_mount_path(league_slug)
    path = u if u.startswith("/") else f"/{u}"
    if mount and (path == mount or path.startswith(f"{mount}/")):
        path = path[len(mount) :] or "/"
        if not path.startswith("/"):
            path = f"/{path}"
    return f"{base}{mount}{path}"


def sanitize_discord_event_payload(league_slug: str, payload: dict) -> dict:
    """Return payload copy safe for Discord (absolute or omitted embed link)."""
    out = dict(payload or {})
    raw_url = str(out.get("url") or "").strip()
    fixed = normalize_discord_payload_url(league_slug, raw_url)
    article_id = out.get("article_id")
    if article_id is not None:
        article_url = build_news_article_public_url(league_slug, article_id)
        if article_url and (
            not fixed
            or "league-headlines#a" not in raw_url.lower()
        ):
            fixed = article_url
    if fixed:
        out["url"] = fixed
    else:
        out.pop("url", None)
    return out


def _source_idempotency_key(
    *, league_slug: str, event_key: str, source_type: str, source_id: str
) -> str:
    material = json.dumps(
        {
            "league_slug": str(league_slug or ""),
            "event_key": str(event_key or ""),
            "source_type": str(source_type or ""),
            "source_id": str(source_id or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:64]


def bowl_six_leaders_idempotency_key(*, league_slug: str, slate_id: int) -> str:
    return f"bowl-six-leaders:{str(league_slug or '').strip()}:{int(slate_id)}"


def _event_idempotency_key(*, league_slug: str, event_key: str, channel_key: str, payload: dict) -> str:
    material = json.dumps(
        {
            "league_slug": str(league_slug or ""),
            "event_key": str(event_key or ""),
            "channel_key": str(channel_key or ""),
            "payload": payload or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:64]


def _route_map(session, league_slug: str) -> dict[str, DiscordChannelRoute]:
    rows = session.scalars(
        select(DiscordChannelRoute).where(DiscordChannelRoute.league_slug == league_slug)
    ).all()
    return {str(r.event_key): r for r in rows}


def _migrate_ops_request_to_trade_request(session) -> None:
    """Rename legacy ops_request_status routes/events to trade_request (per-league, no duplicate key)."""
    legacy_routes = session.scalars(
        select(DiscordChannelRoute).where(DiscordChannelRoute.event_key == "ops_request_status")
    ).all()
    for row in legacy_routes:
        slug = str(row.league_slug or "")
        trade = session.scalar(
            select(DiscordChannelRoute).where(
                DiscordChannelRoute.league_slug == slug,
                DiscordChannelRoute.event_key == "trade_request",
            )
        )
        if trade is not None:
            session.delete(row)
        else:
            row.event_key = "trade_request"
    ev_upd = session.execute(
        update(DiscordOutboundEvent)
        .where(DiscordOutboundEvent.event_key == "ops_request_status")
        .values(event_key="trade_request")
    )
    if legacy_routes or (getattr(ev_upd, "rowcount", 0) or 0) > 0:
        session.commit()


def bootstrap_discord_integration_all_leagues(session) -> None:
    """Ensure bot config + default routes exist for every league (blank guild/channel IDs)."""
    from app.config import league_slugs

    for slug in league_slugs():
        _ensure_discord_bot_cfg_row(session, str(slug).strip())
        ensure_discord_routes(session, str(slug).strip())
    session.commit()


def ensure_discord_routes(session, league_slug: str, updated_by_user_id: int | None = None) -> None:
    _migrate_ops_request_to_trade_request(session)
    by_key = _route_map(session, league_slug)
    suppressed = _suppressed_default_route_keys(session, league_slug)
    now = datetime.utcnow()
    changed = False
    for key in sorted(DEFAULT_EVENT_KEYS):
        if key in suppressed:
            continue
        if key in by_key:
            continue
        session.add(
            DiscordChannelRoute(
                league_slug=league_slug,
                event_key=key,
                channel_key=DEFAULT_EVENT_CHANNEL_KEY.get(key, ""),
                discord_channel_id="",
                label=DEFAULT_EVENT_LABELS.get(key, ""),
                description="",
                is_enabled=True,
                updated_by_user_id=updated_by_user_id,
                updated_at=now,
            )
        )
        changed = True
    if changed:
        session.commit()


def list_discord_routes(session, league_slug: str) -> list[DiscordChannelRoute]:
    ensure_discord_routes(session, league_slug)
    return session.scalars(
        select(DiscordChannelRoute)
        .where(DiscordChannelRoute.league_slug == league_slug)
        .order_by(DiscordChannelRoute.event_key.asc(), DiscordChannelRoute.id.asc())
    ).all()


def get_league_bot_config(session, league_slug: str) -> DiscordLeagueBotConfig:
    row = session.scalar(
        select(DiscordLeagueBotConfig).where(DiscordLeagueBotConfig.league_slug == league_slug).limit(1)
    )
    if row is not None:
        return row
    row = DiscordLeagueBotConfig(
        league_slug=league_slug,
        guild_id="",
        is_enabled=True,
        notes="",
        suppressed_default_route_keys_json="[]",
        updated_by_user_id=None,
        updated_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def update_league_bot_config(
    session,
    *,
    league_slug: str,
    guild_id: str,
    is_enabled: bool,
    notes: str,
    updated_by_user_id: int,
) -> DiscordLeagueBotConfig:
    row = get_league_bot_config(session, league_slug)
    gid = str(guild_id or "").strip()
    if gid and not DISCORD_SNOWFLAKE_PATTERN.match(gid):
        raise ValueError("guild_id must be a numeric Discord snowflake")
    row.guild_id = gid[:64]
    row.is_enabled = bool(is_enabled)
    row.notes = str(notes or "")[:2000]
    row.updated_by_user_id = int(updated_by_user_id)
    row.updated_at = datetime.utcnow()
    session.commit()
    return row


def update_discord_routes(session, league_slug: str, rows: list[dict], updated_by_user_id: int) -> list[dict]:
    ensure_discord_routes(session, league_slug, updated_by_user_id=updated_by_user_id)
    existing = _route_map(session, league_slug)
    now = datetime.utcnow()
    for item in rows:
        key = str(item.get("event_key") or "").strip()
        row = existing.get(key)
        if row is None:
            continue
        row.channel_key = str(item.get("channel_key") or "").strip()[:64]
        cid = str(item.get("discord_channel_id") or "").strip()
        if cid and not is_valid_discord_channel_id(cid):
            continue
        row.discord_channel_id = cid[:32]
        row.label = str(item.get("label") or row.label or "").strip()[:120]
        row.description = str(item.get("description") or row.description or "").strip()[:2000]
        row.is_enabled = bool(item.get("is_enabled"))
        row.updated_by_user_id = int(updated_by_user_id)
        row.updated_at = now
    session.commit()
    return [
        {
            "event_key": r.event_key,
            "channel_key": r.channel_key,
            "discord_channel_id": r.discord_channel_id,
            "label": r.label,
            "is_enabled": bool(r.is_enabled),
        }
        for r in list_discord_routes(session, league_slug)
    ]


def add_discord_route(
    session,
    *,
    league_slug: str,
    event_key: str,
    channel_key: str,
    discord_channel_id: str = "",
    label: str = "",
    description: str = "",
    is_enabled: bool = True,
    updated_by_user_id: int,
) -> DiscordChannelRoute:
    key = str(event_key or "").strip()
    if not is_valid_event_key(key):
        raise ValueError("Invalid event_key")
    cid = str(discord_channel_id or "").strip()
    if cid and not is_valid_discord_channel_id(cid):
        raise ValueError("Invalid discord_channel_id")
    ensure_discord_routes(session, league_slug, updated_by_user_id=updated_by_user_id)
    existing = _route_map(session, league_slug).get(key)
    if existing is not None:
        raise ValueError("Route already exists for this event_key")
    _forget_removed_default_route(session, league_slug, key)
    now = datetime.utcnow()
    row = DiscordChannelRoute(
        league_slug=league_slug,
        event_key=key,
        channel_key=str(channel_key or DEFAULT_EVENT_CHANNEL_KEY.get(key, "")).strip()[:64],
        discord_channel_id=cid[:32],
        label=str(label or DEFAULT_EVENT_LABELS.get(key, "")).strip()[:120],
        description=str(description or "").strip()[:2000],
        is_enabled=bool(is_enabled),
        updated_by_user_id=int(updated_by_user_id),
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def delete_discord_route(session, *, league_slug: str, event_key: str) -> bool:
    key = str(event_key or "").strip()
    row = session.scalar(
        select(DiscordChannelRoute).where(
            DiscordChannelRoute.league_slug == league_slug,
            DiscordChannelRoute.event_key == key,
        )
    )
    if row is None:
        return False
    session.delete(row)
    _remember_removed_default_route(session, league_slug, key)
    session.commit()
    return True


def is_source_delivered(session, *, league_slug: str, source_type: str, source_id: str) -> bool:
    st = str(source_type or "").strip()
    sid = str(source_id or "").strip()
    if not st or not sid:
        return False
    row = session.scalar(
        select(DiscordDeliveredSource).where(
            DiscordDeliveredSource.league_slug == league_slug,
            DiscordDeliveredSource.source_type == st,
            DiscordDeliveredSource.source_id == sid,
        )
    )
    return row is not None


def record_delivered_source(
    session,
    *,
    league_slug: str,
    source_type: str,
    source_id: str,
    event_key: str = "",
    outbound_event_id: int | None = None,
) -> DiscordDeliveredSource | None:
    st = str(source_type or "").strip()
    sid = str(source_id or "").strip()
    if not st or not sid:
        return None
    existing = session.scalar(
        select(DiscordDeliveredSource).where(
            DiscordDeliveredSource.league_slug == league_slug,
            DiscordDeliveredSource.source_type == st,
            DiscordDeliveredSource.source_id == sid,
        )
    )
    if existing is not None:
        return existing
    row = DiscordDeliveredSource(
        league_slug=league_slug,
        source_type=st[:64],
        source_id=sid[:64],
        event_key=str(event_key or "")[:64],
        outbound_event_id=outbound_event_id,
        delivered_at=datetime.utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _payload_with_source(payload: dict, *, source_type: str | None, source_id: str | int | None) -> dict:
    out = dict(payload or {})
    st = str(source_type or out.get("source_type") or "").strip()
    sid_raw = source_id if source_id is not None else out.get("source_id")
    sid = str(sid_raw).strip() if sid_raw is not None and str(sid_raw).strip() else ""
    if st:
        out["source_type"] = st
    if sid:
        out["source_id"] = sid
    return out


def enqueue_discord_event(
    session,
    *,
    league_slug: str,
    event_key: str,
    payload: dict,
    created_by_user_id: int | None,
    source_type: str | None = None,
    source_id: str | int | None = None,
) -> DiscordOutboundEvent | None:
    key = str(event_key or "").strip()
    if not is_valid_event_key(key):
        return None
    ensure_discord_routes(session, league_slug)
    route = _route_map(session, league_slug).get(key)
    if route is None or not bool(route.is_enabled):
        return None
    bot_cfg = get_league_bot_config(session, league_slug)
    if not bool(bot_cfg.is_enabled):
        return None
    payload_clean = _payload_with_source(payload, source_type=source_type, source_id=source_id)
    st = str(payload_clean.get("source_type") or "").strip()
    sid = str(payload_clean.get("source_id") or "").strip()
    if st and sid:
        if is_source_delivered(session, league_slug=league_slug, source_type=st, source_id=sid):
            return None
    channel_key = str(route.channel_key or DEFAULT_EVENT_CHANNEL_KEY.get(key, ""))
    if st and sid:
        idem_key = _source_idempotency_key(
            league_slug=league_slug, event_key=key, source_type=st, source_id=sid
        )
    else:
        idem_key = _event_idempotency_key(
            league_slug=league_slug,
            event_key=key,
            channel_key=channel_key,
            payload=payload_clean,
        )
    existing = session.scalar(
        select(DiscordOutboundEvent)
        .where(
            DiscordOutboundEvent.league_slug == league_slug,
            DiscordOutboundEvent.idempotency_key == idem_key,
            DiscordOutboundEvent.status.in_(("pending", "sent", "failed")),
        )
        .order_by(DiscordOutboundEvent.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    row = DiscordOutboundEvent(
        league_slug=league_slug,
        event_key=key,
        channel_key=channel_key,
        idempotency_key=idem_key,
        payload_json=json.dumps(payload_clean),
        status="pending",
        attempts=0,
        last_error="",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        next_attempt_at=None,
        sent_at=None,
    )
    session.add(row)
    session.flush()
    return row


def enqueue_repeatable_discord_event(
    session,
    *,
    league_slug: str,
    event_key: str,
    payload: dict,
    created_by_user_id: int | None,
    slate_id: int,
) -> DiscordOutboundEvent | None:
    """Queue a live-updating Discord post (replaces pending; allows repeat delivery)."""
    key = str(event_key or "").strip()
    if key not in REPEATABLE_DISCORD_EVENT_KEYS or not is_valid_event_key(key):
        return None
    ensure_discord_routes(session, league_slug)
    route = _route_map(session, league_slug).get(key)
    if route is None or not bool(route.is_enabled):
        return None
    bot_cfg = get_league_bot_config(session, league_slug)
    if not bool(bot_cfg.is_enabled):
        return None
    payload_clean = dict(payload or {})
    channel_key = str(route.channel_key or DEFAULT_EVENT_CHANNEL_KEY.get(key, ""))
    idem_key = bowl_six_leaders_idempotency_key(league_slug=league_slug, slate_id=int(slate_id))
    pending = session.scalar(
        select(DiscordOutboundEvent)
        .where(
            DiscordOutboundEvent.league_slug == league_slug,
            DiscordOutboundEvent.idempotency_key == idem_key,
            DiscordOutboundEvent.status == "pending",
        )
        .order_by(DiscordOutboundEvent.id.desc())
        .limit(1)
    )
    if pending is not None:
        pending.event_key = key
        pending.channel_key = channel_key
        pending.payload_json = json.dumps(payload_clean)
        pending.attempts = 0
        pending.last_error = ""
        pending.next_attempt_at = None
        pending.created_at = datetime.utcnow()
        session.flush()
        return pending
    row = DiscordOutboundEvent(
        league_slug=league_slug,
        event_key=key,
        channel_key=channel_key,
        idempotency_key=idem_key,
        payload_json=json.dumps(payload_clean),
        status="pending",
        attempts=0,
        last_error="",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        next_attempt_at=None,
        sent_at=None,
    )
    session.add(row)
    session.flush()
    return row


def list_outbound_events(
    session, *, league_slug: str, status: str = "", event_key: str = "", limit: int = 250
) -> list[DiscordOutboundEvent]:
    q = select(DiscordOutboundEvent).where(DiscordOutboundEvent.league_slug == league_slug)
    st = str(status or "").strip().lower()
    if st in {"pending", "sent", "failed", "cancelled"}:
        q = q.where(DiscordOutboundEvent.status == st)
    ek = str(event_key or "").strip()
    if ek:
        q = q.where(DiscordOutboundEvent.event_key == ek)
    return session.scalars(
        q.order_by(DiscordOutboundEvent.created_at.desc(), DiscordOutboundEvent.id.desc()).limit(max(1, int(limit)))
    ).all()


def _parse_payload(row: DiscordOutboundEvent) -> dict:
    try:
        return json.loads(row.payload_json or "{}")
    except Exception:
        return {}


def fetch_pending_events_for_bot(session, *, league_slug: str, limit: int = 20) -> list[DiscordOutboundEvent]:
    now = datetime.utcnow()
    rows = session.scalars(
        select(DiscordOutboundEvent)
        .where(
            DiscordOutboundEvent.league_slug == league_slug,
            DiscordOutboundEvent.status == "pending",
            or_(DiscordOutboundEvent.next_attempt_at.is_(None), DiscordOutboundEvent.next_attempt_at <= now),
        )
        .order_by(DiscordOutboundEvent.created_at.asc(), DiscordOutboundEvent.id.asc())
        .limit(max(1, min(100, int(limit) * 2)))
    ).all()
    out: list[DiscordOutboundEvent] = []
    changed = False
    for row in rows:
        payload = _parse_payload(row)
        st = str(payload.get("source_type") or "").strip()
        sid = str(payload.get("source_id") or "").strip()
        ek = str(row.event_key or "")
        if (
            ek not in REPEATABLE_DISCORD_EVENT_KEYS
            and st
            and sid
            and is_source_delivered(session, league_slug=league_slug, source_type=st, source_id=sid)
        ):
            row.status = "sent"
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = ""
            row.next_attempt_at = None
            row.sent_at = datetime.utcnow()
            changed = True
            continue
        out.append(row)
        if len(out) >= max(1, min(100, int(limit))):
            break
    if changed:
        session.commit()
    return out


def bot_event_delivery_fields(session, *, league_slug: str, event_key: str) -> dict[str, str]:
    routes = _route_map(session, league_slug)
    cfg = get_league_bot_config(session, league_slug)
    return bot_event_delivery_fields_cached(
        routes, cfg, event_key=event_key
    )


def bot_event_delivery_fields_cached(
    routes: dict[str, DiscordChannelRoute],
    bot_cfg: DiscordLeagueBotConfig,
    *,
    event_key: str,
) -> dict[str, str]:
    route = routes.get(str(event_key or ""))
    return {
        "discord_channel_id": str(route.discord_channel_id or "") if route else "",
        "guild_id": str(bot_cfg.guild_id or ""),
        "channel_key": str(route.channel_key or "") if route else "",
    }


def serialize_pending_events_for_bot(
    session,
    *,
    league_slug: str,
    rows: list[DiscordOutboundEvent],
) -> list[dict]:
    """Build bot JSON for pending rows (one route-map load per request)."""
    routes = _route_map(session, league_slug)
    bot_cfg = get_league_bot_config(session, league_slug)
    guild_default = str(bot_cfg.guild_id or "")
    out: list[dict] = []
    for r in rows:
        try:
            raw_payload = json.loads(r.payload_json or "{}")
            payload = enrich_discord_payload_for_bot(
                session,
                league_slug=league_slug,
                event_key=str(r.event_key or ""),
                payload=raw_payload,
            )
            payload = sanitize_discord_event_payload(league_slug, payload)
            if str(r.event_key or "") in OPS_TEXT_ONLY_DISCORD_EVENT_KEYS:
                payload.pop("url", None)
        except Exception:
            payload = {}
        delivery = bot_event_delivery_fields_cached(
            routes, bot_cfg, event_key=str(r.event_key or "")
        )
        out.append(
            {
                "id": int(r.id),
                "league_slug": str(r.league_slug or ""),
                "event_key": str(r.event_key or ""),
                "channel_key": str(r.channel_key or ""),
                "discord_channel_id": delivery.get("discord_channel_id") or "",
                "guild_id": delivery.get("guild_id") or guild_default,
                "idempotency_key": str(r.idempotency_key or ""),
                "payload": payload,
                "attempts": int(r.attempts or 0),
                "created_at": r.created_at.isoformat(timespec="seconds")
                if r.created_at
                else None,
            }
        )
    return out


def mark_event_sent(session, event_id: int, *, discord_message_id: str = "") -> bool:
    row = session.get(DiscordOutboundEvent, int(event_id))
    if row is None or str(row.status) in {"cancelled", "sent"}:
        return False
    payload = _parse_payload(row)
    st = str(payload.get("source_type") or "").strip()
    sid = str(payload.get("source_id") or "").strip()
    ek = str(row.event_key or "")
    mid = str(discord_message_id or "").strip()
    if mid:
        from app.services.bowl_six_discord import record_bowl_six_leaders_discord_ack

        record_bowl_six_leaders_discord_ack(
            session, event_key=ek, payload=payload, discord_message_id=mid
        )
    if ek not in REPEATABLE_DISCORD_EVENT_KEYS and st and sid:
        record_delivered_source(
            session,
            league_slug=str(row.league_slug or ""),
            source_type=st,
            source_id=sid,
            event_key=ek,
            outbound_event_id=int(row.id),
        )
    row.status = "sent"
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = ""
    row.next_attempt_at = None
    row.sent_at = datetime.utcnow()
    session.commit()
    return True


def mark_event_failed(session, event_id: int, error: str) -> bool:
    row = session.get(DiscordOutboundEvent, int(event_id))
    if row is None or str(row.status) == "cancelled":
        return False
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = str(error or "").strip()[:1200]
    if int(row.attempts) >= MAX_DELIVERY_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
    else:
        delay_minutes = max(1, min(15, (2 ** max(0, int(row.attempts) - 1)) + (int(row.attempts) - 1)))
        row.status = "pending"
        row.next_attempt_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
    session.commit()
    return True


def canonical_discord_bot_name() -> str:
    """Worker identity for scripts/league_discord_bot (DISCORD_BOT_NAME)."""
    if has_app_context():
        name = str(current_app.config.get("DISCORD_BOT_NAME") or "").strip()
        if name:
            return name[:120]
    return (
        os.environ.get("DISCORD_BOT_NAME", "league-discord-bot").strip()[:120]
        or "league-discord-bot"
    )


def prune_obsolete_discord_bot_heartbeats(
    session, *, league_slug: str | None = None
) -> int:
    """Remove legacy per-league bot rows (e.g. bowl-historical-bot) after unified worker rollout."""
    canonical = canonical_discord_bot_name()
    stmt = delete(DiscordBotHeartbeat).where(DiscordBotHeartbeat.bot_name != canonical)
    if league_slug:
        stmt = stmt.where(DiscordBotHeartbeat.league_slug == str(league_slug).strip())
    result = session.execute(stmt)
    session.commit()
    return int(result.rowcount or 0)


def upsert_bot_heartbeat(
    session,
    *,
    league_slug: str,
    bot_name: str,
    bot_version: str,
    guild_id: str,
    extra: dict | None = None,
) -> DiscordBotHeartbeat:
    row = session.scalar(
        select(DiscordBotHeartbeat)
        .where(
            DiscordBotHeartbeat.league_slug == league_slug,
            DiscordBotHeartbeat.bot_name == str(bot_name or ""),
        )
        .limit(1)
    )
    if row is None:
        row = DiscordBotHeartbeat(
            league_slug=league_slug,
            bot_name=str(bot_name or "")[:120],
            bot_version=str(bot_version or "")[:64],
            guild_id=str(guild_id or "")[:64],
            last_seen_at=datetime.utcnow(),
            extra_json=json.dumps(extra or {}),
        )
        session.add(row)
    else:
        row.bot_version = str(bot_version or "")[:64]
        row.guild_id = str(guild_id or "")[:64]
        row.last_seen_at = datetime.utcnow()
        row.extra_json = json.dumps(extra or {})
    session.commit()
    if str(bot_name or "").strip() == canonical_discord_bot_name():
        prune_obsolete_discord_bot_heartbeats(session, league_slug=league_slug)
    return row


def list_heartbeats(session, *, league_slug: str, limit: int = 10) -> list[DiscordBotHeartbeat]:
    canonical = canonical_discord_bot_name()
    return session.scalars(
        select(DiscordBotHeartbeat)
        .where(
            DiscordBotHeartbeat.league_slug == league_slug,
            DiscordBotHeartbeat.bot_name == canonical,
        )
        .order_by(DiscordBotHeartbeat.last_seen_at.desc(), DiscordBotHeartbeat.id.desc())
        .limit(max(1, int(limit)))
    ).all()
