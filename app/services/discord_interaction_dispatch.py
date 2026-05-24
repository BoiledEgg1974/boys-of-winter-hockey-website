"""Route Discord interactions to the correct league app by guild (server) id."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

import httpx
from flask import Flask
from sqlalchemy import select

from app.config import league_by_slug, make_league_config
from app.league_db import db
from app.services.discord_interactions import (
    handle_slash_interaction,
    verify_interaction_signature,
)
from app.site_models import DiscordLeagueBotConfig

log = logging.getLogger(__name__)

_league_apps: dict[str, Flask] = {}
_prewarm_started = False


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
        .where(DiscordLeagueBotConfig.guild_id == gid)
        .order_by(DiscordLeagueBotConfig.is_enabled.desc(), DiscordLeagueBotConfig.id.asc())
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


def prewarm_league_apps() -> None:
    """Load league Flask apps in a background thread (first interaction is faster)."""
    global _prewarm_started
    if _prewarm_started:
        return
    _prewarm_started = True

    def _run() -> None:
        from app.config import league_slugs

        for slug in league_slugs():
            try:
                _league_app(slug)
                log.info("prewarmed discord interaction app for %s", slug)
            except Exception:
                log.exception("prewarm failed for %s", slug)

    threading.Thread(target=_run, name="discord-prewarm", daemon=True).start()


def _ephemeral_error(content: str) -> dict[str, Any]:
    return {"type": 4, "data": {"content": content[:1900], "flags": 64}}


def _deferred_ephemeral() -> dict[str, Any]:
    return {"type": 5, "data": {"flags": 64}}


def _content_from_handler_response(response: dict[str, Any]) -> str:
    if int(response.get("type") or 0) == 4:
        return str((response.get("data") or {}).get("content") or "").strip()
    return str(response.get("content") or "").strip()


def _post_interaction_followup(application_id: str, interaction_token: str, content: str) -> None:
    app_id = str(application_id or "").strip()
    token = str(interaction_token or "").strip()
    if not app_id or not token:
        log.warning("cannot post interaction followup: missing application_id or token")
        return
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
    resp = httpx.post(
        url,
        json={"content": content[:1900] or "Done.", "flags": 64},
        timeout=15.0,
    )
    if resp.status_code >= 400:
        log.warning(
            "interaction followup failed status=%s body=%s",
            resp.status_code,
            resp.text[:500],
        )


def _run_slash_command_async(
    *,
    hub_app: Flask,
    payload: dict[str, Any],
    league_slug: str,
) -> None:
    application_id = str(payload.get("application_id") or "").strip()
    interaction_token = str(payload.get("token") or "").strip()

    def _work() -> None:
        try:
            with hub_app.app_context():
                league_app = _league_app(league_slug)
                with league_app.app_context():
                    response = handle_slash_interaction(payload, league_slug=league_slug)
                content = _content_from_handler_response(response)
                if not content:
                    content = "Command finished but returned no message."
            _post_interaction_followup(application_id, interaction_token, content)
        except Exception:
            log.exception("async slash command failed for %s", league_slug)
            _post_interaction_followup(
                application_id,
                interaction_token,
                "Something went wrong running that command. Try again in a moment.",
            )

    threading.Thread(target=_work, name=f"discord-cmd-{league_slug}", daemon=True).start()


def process_discord_interaction(
    *,
    raw_body: bytes,
    timestamp: str,
    signature: str,
    public_key: str,
    shared_secret: str,
    hub_app: Flask | None = None,
    defer_slash_commands: bool = True,
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
        return 401, {"error": "Discord interactions public key is not configured"}

    try:
        payload = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "invalid json"}

    if int(payload.get("type") or 0) == 1:
        if hub_app is not None:
            prewarm_league_apps()
        return 200, {"type": 1}

    guild_id = guild_id_from_interaction(payload)
    if not guild_id:
        return 200, _ephemeral_error(
            "Run this command in your league's Discord server so I know which BOWL site to use. "
            "DM commands are not supported yet."
        )

    lookup_app = hub_app
    if lookup_app is None:
        from flask import current_app

        lookup_app = current_app._get_current_object()  # type: ignore[attr-defined]

    with lookup_app.app_context():
        league_slug = league_slug_for_guild_id(guild_id)

    if not league_slug:
        return 200, _ephemeral_error(
            "This Discord server is not linked to a BOWL league yet. "
            "An admin can add the Server ID on that league's Discord Integration page "
            "(Admin → Discord Integration → Server ID)."
        )

    if defer_slash_commands and hub_app is not None and int(payload.get("type") or 0) == 2:
        prewarm_league_apps()
        _run_slash_command_async(hub_app=hub_app, payload=payload, league_slug=league_slug)
        return 200, _deferred_ephemeral()

    league_app = _league_app(league_slug)
    with league_app.app_context():
        return 200, handle_slash_interaction(payload, league_slug=league_slug)


def clear_league_app_cache() -> None:
    """Test helper: drop cached league Flask apps."""
    global _prewarm_started
    _league_apps.clear()
    _prewarm_started = False
