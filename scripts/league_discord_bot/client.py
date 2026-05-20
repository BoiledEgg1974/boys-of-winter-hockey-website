from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from scripts.league_discord_bot.config import BotSettings
from scripts.league_discord_bot.formatters import format_discord_messages, sanitize_discord_message_body

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

    def poll_pending(
        self, client: httpx.Client, league_slug: str, *, site_timeout: float | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        url = self._site_url(league_slug, "/api/discord/events/pending")
        timeout = site_timeout if site_timeout is not None else self.settings.site_timeout_seconds
        try:
            resp = client.get(
                url,
                params={"league_slug": league_slug, "limit": 20},
                headers=self._headers,
                timeout=timeout,
            )
        except httpx.ReadTimeout:
            log.warning(
                "pending fetch timed out for %s after %.0fs; retrying once",
                league_slug,
                float(timeout),
            )
            resp = client.get(
                url,
                params={"league_slug": league_slug, "limit": 20},
                headers=self._headers,
                timeout=timeout,
            )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("message") or "pending fetch failed")
        return list(data.get("events") or []), str(data.get("guild_id") or "").strip()

    def _post_discord_once(
        self, discord_client: httpx.Client, channel_id: str, body: dict[str, Any]
    ) -> httpx.Response:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        return discord_client.post(url, headers=self._discord_headers, json=body)

    def _patch_discord_once(
        self,
        discord_client: httpx.Client,
        channel_id: str,
        message_id: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        url = (
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
        )
        return discord_client.patch(url, headers=self._discord_headers, json=body)

    def _discord_request_with_retry(
        self,
        discord_client: httpx.Client,
        request_fn,
        *,
        channel_id: str,
    ) -> httpx.Response:
        resp = request_fn()
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
            resp = request_fn()
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Discord API {resp.status_code}: {detail}")
        return resp

    def post_discord(
        self, discord_client: httpx.Client, channel_id: str, body: dict[str, Any]
    ) -> str:
        body = sanitize_discord_message_body(body)
        resp = self._discord_request_with_retry(
            discord_client,
            lambda: self._post_discord_once(discord_client, channel_id, body),
            channel_id=channel_id,
        )
        try:
            return str((resp.json() or {}).get("id") or "").strip()
        except Exception:
            return ""

    def patch_discord(
        self,
        discord_client: httpx.Client,
        channel_id: str,
        message_id: str,
        body: dict[str, Any],
    ) -> str:
        body = sanitize_discord_message_body(body)
        resp = self._discord_request_with_retry(
            discord_client,
            lambda: self._patch_discord_once(
                discord_client, channel_id, message_id, body
            ),
            channel_id=channel_id,
        )
        try:
            return str((resp.json() or {}).get("id") or message_id or "").strip()
        except Exception:
            return str(message_id or "").strip()

    def ack(
        self,
        client: httpx.Client,
        league_slug: str,
        event_id: int,
        *,
        discord_message_id: str = "",
    ) -> None:
        url = self._site_url(league_slug, f"/api/discord/events/{event_id}/ack")
        body: dict[str, str] = {}
        mid = str(discord_message_id or "").strip()
        if mid:
            body["discord_message_id"] = mid
        resp = client.post(url, headers=self._headers, json=body or None)
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

    def deliver_one(
        self,
        site_client: httpx.Client,
        discord_client: httpx.Client,
        league_slug: str,
        event: dict[str, Any],
    ) -> None:
        event_id = int(event["id"])
        channel_id = str(event.get("discord_channel_id") or "").strip()
        if not channel_id:
            raise RuntimeError(f"Event {event_id} missing discord_channel_id in route config")
        payload = event.get("payload") or {}
        edit_message_id = str(payload.get("edit_message_id") or "").strip()
        bodies = format_discord_messages(event, max_parts=self.settings.max_message_parts)
        delay = float(self.settings.delivery_delay_seconds)
        delivered_message_id = ""
        for i, body in enumerate(bodies):
            if i > 0 and delay > 0:
                time.sleep(delay)
            if i == 0 and edit_message_id:
                delivered_message_id = self.patch_discord(
                    discord_client, channel_id, edit_message_id, body
                )
            else:
                delivered_message_id = self.post_discord(
                    discord_client, channel_id, body
                )
        self.ack(
            site_client,
            league_slug,
            event_id,
            discord_message_id=delivered_message_id,
        )

    def run_cycle(self, site_client: httpx.Client, discord_client: httpx.Client) -> str | None:
        last_error: str | None = None
        delay = float(self.settings.delivery_delay_seconds)
        for slug in sorted(self.settings.league_base_urls):
            try:
                events, league_guild_id = self.poll_pending(site_client, slug)
                guild_id = league_guild_id
                for idx, ev in enumerate(events):
                    guild_id = str(ev.get("guild_id") or guild_id) or guild_id
                    if idx > 0 and delay > 0:
                        time.sleep(delay)
                    try:
                        self.deliver_one(site_client, discord_client, slug, ev)
                        log.info("delivered event %s for %s", ev.get("id"), slug)
                    except Exception as exc:
                        last_error = str(exc)
                        log.warning("delivery failed event %s %s: %s", ev.get("id"), slug, exc)
                        try:
                            self.fail(site_client, slug, int(ev["id"]), last_error)
                        except Exception:
                            log.exception("fail report failed for event %s", ev.get("id"))
                try:
                    self.heartbeat(
                        site_client,
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
            "Starting league_discord_bot for leagues: %s (delay=%.1fs, max_parts=%s, site_timeout=%.0fs)",
            ", ".join(sorted(self.settings.league_base_urls)),
            self.settings.delivery_delay_seconds,
            self.settings.max_message_parts,
            self.settings.site_timeout_seconds,
        )
        site_timeout = httpx.Timeout(
            connect=15.0,
            read=float(self.settings.site_timeout_seconds),
            write=30.0,
            pool=30.0,
        )
        discord_timeout = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=30.0)
        with httpx.Client(timeout=site_timeout) as site_client, httpx.Client(
            timeout=discord_timeout
        ) as discord_client:
            while True:
                self.run_cycle(site_client, discord_client)
                time.sleep(max(2.0, float(self.settings.poll_seconds)))
