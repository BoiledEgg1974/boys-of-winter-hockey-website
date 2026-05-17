from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from scripts.league_discord_bot.config import BotSettings
from scripts.league_discord_bot.formatters import format_discord_messages

log = logging.getLogger(__name__)


class LeagueDiscordBot:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self._headers = {
            "X-Discord-Events-Secret": settings.shared_secret,
            "Accept": "application/json",
        }
        self._discord_headers = {
            "Authorization": f"Bot {settings.token}",
            "Content-Type": "application/json",
        }

    def _site_url(self, league_slug: str, path: str) -> str:
        base = self.settings.league_base_urls.get(league_slug, "").rstrip("/")
        if not base:
            raise KeyError(f"No base URL configured for league {league_slug}")
        rel = path if path.startswith("/") else f"/{path}"
        return f"{base}{rel}"

    def poll_pending(self, client: httpx.Client, league_slug: str) -> tuple[list[dict[str, Any]], str]:
        url = self._site_url(league_slug, "/api/discord/events/pending")
        resp = client.get(url, params={"league_slug": league_slug, "limit": 20}, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("message") or "pending fetch failed")
        return list(data.get("events") or []), str(data.get("guild_id") or "").strip()

    def _post_discord_once(self, client: httpx.Client, channel_id: str, body: dict[str, Any]) -> httpx.Response:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        return client.post(url, headers=self._discord_headers, json=body)

    def post_discord(self, client: httpx.Client, channel_id: str, body: dict[str, Any]) -> None:
        resp = self._post_discord_once(client, channel_id, body)
        if resp.status_code == 429:
            retry_after = 2.0
            try:
                detail = resp.json()
                retry_after = float(detail.get("retry_after", retry_after))
            except Exception:
                raw = resp.headers.get("Retry-After")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        pass
            log.warning("Discord 429 on channel %s; sleeping %.1fs", channel_id, retry_after)
            time.sleep(max(0.5, retry_after))
            resp = self._post_discord_once(client, channel_id, body)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Discord API {resp.status_code}: {detail}")

    def ack(self, client: httpx.Client, league_slug: str, event_id: int) -> None:
        url = self._site_url(league_slug, f"/api/discord/events/{event_id}/ack")
        resp = client.post(url, headers=self._headers)
        resp.raise_for_status()

    def fail(self, client: httpx.Client, league_slug: str, event_id: int, error: str) -> None:
        url = self._site_url(league_slug, f"/api/discord/events/{event_id}/fail")
        resp = client.post(url, headers=self._headers, json={"error": error[:1200]})
        resp.raise_for_status()

    def heartbeat(
        self,
        client: httpx.Client,
        *,
        league_slug: str,
        guild_id: str,
        pending_count: int,
        last_error: str = "",
    ) -> None:
        url = self._site_url(league_slug, "/api/discord/events/heartbeat")
        resp = client.post(
            url,
            headers=self._headers,
            json={
                "league_slug": league_slug,
                "bot_name": self.settings.bot_name,
                "bot_version": self.settings.bot_version,
                "guild_id": guild_id,
                "pending_count": pending_count,
                "last_error": last_error,
            },
        )
        resp.raise_for_status()

    def deliver_one(self, client: httpx.Client, league_slug: str, event: dict[str, Any]) -> None:
        event_id = int(event["id"])
        channel_id = str(event.get("discord_channel_id") or "").strip()
        if not channel_id:
            raise RuntimeError(f"Event {event_id} missing discord_channel_id in route config")
        bodies = format_discord_messages(event, max_parts=self.settings.max_message_parts)
        delay = float(self.settings.delivery_delay_seconds)
        for i, body in enumerate(bodies):
            if i > 0 and delay > 0:
                time.sleep(delay)
            self.post_discord(client, channel_id, body)
        self.ack(client, league_slug, event_id)

    def run_cycle(self, client: httpx.Client) -> str | None:
        last_error: str | None = None
        delay = float(self.settings.delivery_delay_seconds)
        for slug in sorted(self.settings.league_base_urls):
            try:
                events, league_guild_id = self.poll_pending(client, slug)
                guild_id = league_guild_id
                for idx, ev in enumerate(events):
                    guild_id = str(ev.get("guild_id") or guild_id) or guild_id
                    if idx > 0 and delay > 0:
                        time.sleep(delay)
                    try:
                        self.deliver_one(client, slug, ev)
                        log.info("delivered event %s for %s", ev.get("id"), slug)
                    except Exception as exc:
                        last_error = str(exc)
                        log.warning("delivery failed event %s %s: %s", ev.get("id"), slug, exc)
                        try:
                            self.fail(client, slug, int(ev["id"]), last_error)
                        except Exception:
                            log.exception("fail report failed for event %s", ev.get("id"))
                try:
                    self.heartbeat(
                        client,
                        league_slug=slug,
                        guild_id=guild_id,
                        pending_count=len(events),
                        last_error=last_error or "",
                    )
                except Exception:
                    log.exception("heartbeat failed for %s", slug)
            except Exception as exc:
                last_error = str(exc)
                log.exception("poll cycle failed for %s", slug)
        return last_error

    def run_forever(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        log.info(
            "Starting league_discord_bot for leagues: %s (delay=%.1fs, max_parts=%s)",
            ", ".join(sorted(self.settings.league_base_urls)),
            self.settings.delivery_delay_seconds,
            self.settings.max_message_parts,
        )
        with httpx.Client(timeout=30.0) as client:
            while True:
                self.run_cycle(client)
                time.sleep(max(2.0, float(self.settings.poll_seconds)))
