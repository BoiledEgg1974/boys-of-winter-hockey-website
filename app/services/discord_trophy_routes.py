"""Trophy Case Discord channel map (BLUP / BLOW private channels on BOWL-Relegation)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.services.discord_events import DISCORD_SNOWFLAKE_PATTERN
from app.services.relegation import is_relegation_league
from app.site_models import DiscordTrophyChannelRoute

# Discord channel names in the trophy-case category (Lady Byng omitted — not used in this league).
RELEGATION_TROPHY_CHANNEL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("blup", "blup-art-ross", "Art Ross Trophy"),
    ("blup", "blup-richard", "Richard Trophy"),
    ("blup", "blup-norris", "Norris Trophy"),
    ("blup", "blup-bourque", "Bourque Trophy"),
    ("blup", "blup-langway", "Langway Trophy"),
    ("blup", "blup-calder", "Calder Trophy"),
    ("blup", "blup-selke", "Selke Trophy"),
    ("blup", "blup-vezina", "Vezina Trophy"),
    ("blup", "blup-crozier", "Roger Crozier Saving Grace Trophy"),
    ("blup", "blup-green-jacket", "The Masters' Green Jacket"),
    ("blup", "blup-plus-minus", "Plus/Minus Trophy"),
    ("blup", "blup-conn-smythe", "Conn Smythe Trophy"),
    ("blow", "blow-lemieux", "Lemieux Trophy (BLOW)"),
    ("blow", "blow-miyazaki", "Miyazaki Trophy (BLOW)"),
    ("blow", "blow-harvey", "Harvey Trophy (BLOW)"),
    ("blow", "blow-orr", "Orr Trophy (BLOW)"),
    ("blow", "blow-pronovost", "Pronovost Trophy (BLOW)"),
    ("blow", "blow-keller", "Keller Trophy (BLOW)"),
    ("blow", "blow-maverick", "Maverick Trophy (BLOW)"),
    ("blow", "blow-binnington", "Binnington Trophy (BLOW)"),
    ("blow", "blow-favell", "Favell Trophy (BLOW)"),
    ("blow", "blow-green-jacket", "The Masters' Green Jacket (BLOW)"),
    ("blow", "blow-plus-minus", "Plus/Minus Trophy (BLOW)"),
    ("blow", "blow-crosby", "Crosby Trophy (BLOW)"),
)

# Legacy BLUP-only keys seeded before BLOW channels existed — removed on ensure.
_OBSOLETE_TROPHY_CHANNEL_KEYS = frozenset(
    {
        "blup-lady-byng",
        "lady-byng",
        "blup-lady_byng",
    }
)


def ensure_relegation_trophy_channel_routes(site_session, league_slug: str) -> int:
    """Create trophy-case rows for bowl-fantasy; drop obsolete Lady Byng mapping."""
    slug = str(league_slug or "").strip()
    if not is_relegation_league(slug):
        return 0
    now = datetime.utcnow()
    created = 0
    existing = {
        str(r.channel_key or "").strip(): r
        for r in site_session.scalars(
            select(DiscordTrophyChannelRoute).where(DiscordTrophyChannelRoute.league_slug == slug)
        ).all()
    }
    for obsolete in _OBSOLETE_TROPHY_CHANNEL_KEYS:
        row = existing.get(obsolete)
        if row is not None:
            site_session.delete(row)
            existing.pop(obsolete, None)
    for tier, channel_key, label in RELEGATION_TROPHY_CHANNEL_SPECS:
        if channel_key in existing:
            row = existing[channel_key]
            if not str(row.label or "").strip():
                row.label = label
                row.tier = tier
            continue
        site_session.add(
            DiscordTrophyChannelRoute(
                league_slug=slug,
                tier=tier,
                channel_key=channel_key,
                label=label,
                discord_channel_id="",
                is_enabled=True,
                updated_by_user_id=None,
                updated_at=now,
            )
        )
        created += 1
    if created:
        from app.sqlite_retry import commit_with_sqlite_retry

        commit_with_sqlite_retry(site_session)
    return created


def list_relegation_trophy_channel_routes(site_session, league_slug: str) -> list[dict]:
    slug = str(league_slug or "").strip()
    ensure_relegation_trophy_channel_routes(site_session, slug)
    rows = site_session.scalars(
        select(DiscordTrophyChannelRoute)
        .where(DiscordTrophyChannelRoute.league_slug == slug)
        .order_by(
            DiscordTrophyChannelRoute.tier.asc(),
            DiscordTrophyChannelRoute.channel_key.asc(),
        )
    ).all()
    return [
        {
            "channel_key": str(r.channel_key or ""),
            "tier": str(r.tier or ""),
            "label": str(r.label or ""),
            "discord_channel_id": str(r.discord_channel_id or ""),
            "is_enabled": bool(r.is_enabled),
        }
        for r in rows
    ]


def update_relegation_trophy_channel_routes(
    site_session,
    league_slug: str,
    rows: list[dict],
    updated_by_user_id: int | None = None,
) -> list[dict]:
    slug = str(league_slug or "").strip()
    ensure_relegation_trophy_channel_routes(site_session, slug)
    by_key = {
        str(r.channel_key or "").strip(): r
        for r in site_session.scalars(
            select(DiscordTrophyChannelRoute).where(DiscordTrophyChannelRoute.league_slug == slug)
        ).all()
    }
    now = datetime.utcnow()
    saved: list[dict] = []
    for item in rows or []:
        key = str(item.get("channel_key") or "").strip()
        if not key or key not in by_key:
            continue
        row = by_key[key]
        cid = str(item.get("discord_channel_id") or "").strip()
        if cid and not DISCORD_SNOWFLAKE_PATTERN.match(cid):
            cid = str(row.discord_channel_id or "").strip()
        row.discord_channel_id = cid[:32]
        row.is_enabled = bool(item.get("is_enabled", True))
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = now
        saved.append(
            {
                "channel_key": key,
                "discord_channel_id": str(row.discord_channel_id or ""),
                "is_enabled": bool(row.is_enabled),
            }
        )
    from app.sqlite_retry import commit_with_sqlite_retry

    commit_with_sqlite_retry(site_session)
    return saved


def resolve_trophy_discord_channel_id(
    site_session,
    *,
    league_slug: str,
    channel_key: str,
) -> str:
    slug = str(league_slug or "").strip()
    key = str(channel_key or "").strip()
    if not slug or not key:
        return ""
    row = site_session.scalar(
        select(DiscordTrophyChannelRoute)
        .where(
            DiscordTrophyChannelRoute.league_slug == slug,
            DiscordTrophyChannelRoute.channel_key == key,
            DiscordTrophyChannelRoute.is_enabled.is_(True),
        )
        .limit(1)
    )
    if row is None:
        return ""
    cid = str(row.discord_channel_id or "").strip()
    return cid if DISCORD_SNOWFLAKE_PATTERN.match(cid) else ""
