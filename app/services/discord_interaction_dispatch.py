"""Route Discord interactions to the correct league app by guild (server) id."""
from __future__ import annotations

import json
from typing import Any

from flask import Flask
from sqlalchemy import select

from app.config import league_by_slug, make_league_config
from app.league_db import db
from app.services.discord_interactions import (
    handle_slash_interaction,
    verify_interaction_signature,
)
from app.site_models import DiscordLeagueBotConfig

_league_apps: dict[str, Flask] = {}


def guild_id_from_interaction(payload: dict[str, Any]) -> str:
    """Discord snowflake for the server where the command was invoked (empty in DMs)."""
    gid = str(payload.get("guild_id") or "").strip()
    if gid:
        return gid
    member = payload.get("member") or {}
    return str(member.get("guild_id") or "").strip()


def league_slug_for_guild_id(guild_id: str) -> str | None:
    gid = str(guild_id or "").strip()
    if not gid:
        return None
    row = db.session.scalar(
        select(DiscordLeagueBotConfig)
        .where(
            DiscordLeagueBotConfig.guild_id == gid,
            DiscordLeagueBotConfig.is_enabled.is_(True),
        )
        .limit(1)
    )
    if row is None:
        return None
    slug = str(row.league_slug or "").strip()
    return slug if league_by_slug(slug) else None


def _league_app(slug: str) -> Flask:
    if slug not in _league_apps:
        from app import create_app

        _league_apps[slug] = create_app(make_league_config(slug))
    return _league_apps[slug]


def _ephemeral_error(content: str) -> dict[str, Any]:
    return {"type": 4, "data": {"content": content[:1900], "flags": 64}}


def process_discord_interaction(
    *,
    raw_body: bytes,
    timestamp: str,
    signature: str,
    public_key: str,
    shared_secret: str,
) -> tuple[int, dict[str, Any]]:
    """Verify signature, resolve league from guild id, return (http_status, json_body)."""
    if public_key:
        if not verify_interaction_signature(
            body=raw_body,
            timestamp=timestamp,
            signature=signature,
            public_key=public_key,
        ):
            return 401, {"error": "invalid request signature"}
    elif not shared_secret:
        return 401, {"error": "Discord interactions public key is not configured"}
    else:
        # Legacy/dev fallback when public key is unset (not used for Discord portal verify).
        return 401, {"error": "Discord interactions public key is not configured"}

    try:
        payload = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "invalid json"}

    if int(payload.get("type") or 0) == 1:
        return 200, {"type": 1}

    guild_id = guild_id_from_interaction(payload)
    if not guild_id:
        return 200, _ephemeral_error(
            "Run this command in your league's Discord server so I know which BOWL site to use. "
            "DM commands are not supported yet."
        )

    league_slug = league_slug_for_guild_id(guild_id)
    if not league_slug:
        return 200, _ephemeral_error(
            "This Discord server is not linked to a BOWL league yet. "
            "An admin can add the Server ID on that league's Discord Integration page."
        )

    league_app = _league_app(league_slug)
    with league_app.app_context():
        return 200, handle_slash_interaction(payload, league_slug=league_slug)


def clear_league_app_cache() -> None:
    """Test helper: drop cached league Flask apps."""
    _league_apps.clear()
