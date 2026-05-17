from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.config import league_slugs

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")


@dataclass(frozen=True)
class BotSettings:
    token: str
    shared_secret: str
    poll_seconds: float
    delivery_delay_seconds: float
    max_message_parts: int
    site_timeout_seconds: float
    bot_name: str
    bot_version: str
    league_base_urls: dict[str, str]


def _parse_league_base_urls(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(raw or "").split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        slug, url = piece.split(":", 1)
        slug = slug.strip()
        url = url.strip().rstrip("/")
        if slug and url:
            out[slug] = url
    return out


def load_settings() -> BotSettings:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    secret = os.environ.get("DISCORD_EVENTS_SHARED_SECRET", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not secret:
        raise RuntimeError("DISCORD_EVENTS_SHARED_SECRET is required")
    urls = _parse_league_base_urls(os.environ.get("DISCORD_BOT_LEAGUE_BASE_URLS", ""))
    public_base = os.environ.get("SITE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    for slug in league_slugs():
        if slug not in urls and public_base:
            urls[slug] = f"{public_base}/{slug}"
    if not urls:
        raise RuntimeError(
            "Set DISCORD_BOT_LEAGUE_BASE_URLS (slug:base_url,...) or SITE_PUBLIC_BASE_URL for all leagues"
        )
    delay_raw = os.environ.get("DISCORD_BOT_DELIVERY_DELAY_SECONDS", "1.2")
    parts_raw = os.environ.get("DISCORD_BOT_MAX_MESSAGE_PARTS", "2")
    site_timeout_raw = os.environ.get("DISCORD_BOT_SITE_TIMEOUT_SECONDS", "90")
    return BotSettings(
        token=token,
        shared_secret=secret,
        poll_seconds=float(os.environ.get("DISCORD_BOT_POLL_SECONDS", "8")),
        delivery_delay_seconds=max(0.0, float(delay_raw)),
        max_message_parts=max(1, min(4, int(parts_raw))),
        site_timeout_seconds=max(30.0, float(site_timeout_raw)),
        bot_name=os.environ.get("DISCORD_BOT_NAME", "league-discord-bot").strip()[:120],
        bot_version=os.environ.get("DISCORD_BOT_VERSION", "1.0.0").strip()[:64],
        league_base_urls=urls,
    )
