from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from scripts.league_discord_bot.config import BotSettings
from scripts.league_discord_bot.formatters import (
    format_direct_message,
    format_discord_messages,
    format_playoff_bracket_deliveries,
    sanitize_discord_message_body,
)

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
        self,
        client: httpx.Client,
        league_slug: str,
        *,
        site_timeout: float | None = None,
        event_key: str = "",
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], str]:
        url = self._site_url(league_slug, "/api/discord/events/pending")
        timeout = site_timeout if site_timeout is not None else self.settings.site_timeout_seconds
        params: dict[str, str | int] = {"league_slug": league_slug, "limit": int(limit)}
        if str(event_key or "").strip():
            params["event_key"] = str(event_key).strip()
        try:
            resp = client.get(
                url,
                params=params,
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
                params=params,
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

    def _create_dm_channel_once(
        self, discord_client: httpx.Client, discord_user_id: str
    ) -> httpx.Response:
        url = "https://discord.com/api/v10/users/@me/channels"
        return discord_client.post(
            url,
            headers=self._discord_headers,
            json={"recipient_id": str(discord_user_id).strip()},
        )

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

    def _get_discord_message_once(
        self, discord_client: httpx.Client, channel_id: str, message_id: str
    ) -> httpx.Response:
        url = (
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
        )
        return discord_client.get(url, headers=self._discord_headers)

    def discord_message_exists(
        self, discord_client: httpx.Client, channel_id: str, message_id: str
    ) -> bool:
        mid = str(message_id or "").strip()
        cid = str(channel_id or "").strip()
        if not mid or not cid:
            return False
        try:
            resp = self._discord_request_with_retry(
                discord_client,
                lambda: self._get_discord_message_once(discord_client, cid, mid),
                channel_id=cid,
            )
        except RuntimeError as exc:
            err = str(exc)
            if "404" in err or "Unknown Message" in err or "10008" in err:
                return False
            raise
        return resp.status_code == 200

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

    def create_dm_channel(self, discord_client: httpx.Client, discord_user_id: str) -> str:
        resp = self._discord_request_with_retry(
            discord_client,
            lambda: self._create_dm_channel_once(discord_client, discord_user_id),
            channel_id=f"dm:{discord_user_id}",
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
        clear_stale_content = bool(body.get("embeds")) and not str(body.get("content") or "").strip()
        body = sanitize_discord_message_body(body)
        if clear_stale_content and body.get("embeds") and "content" not in body:
            # Discord PATCH preserves omitted content; explicitly clear old text when editing
            # an existing BOWL Six post into an embed-only message.
            body["content"] = ""
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
        discord_channel_id: str = "",
        series_deliveries: list[dict[str, Any]] | None = None,
    ) -> None:
        url = self._site_url(league_slug, f"/api/discord/events/{event_id}/ack")
        body: dict[str, Any] = {}
        mid = str(discord_message_id or "").strip()
        if mid:
            body["discord_message_id"] = mid
        cid = str(discord_channel_id or "").strip()
        if cid:
            body["discord_channel_id"] = cid
        if series_deliveries:
            body["series_deliveries"] = series_deliveries
        resp = client.post(url, headers=self._headers, json=body or None)
        resp.raise_for_status()
        try:
            data = resp.json() or {}
        except Exception:
            data = {}
        if data.get("ok") is False:
            raise RuntimeError(str(data.get("message") or "ack rejected by site"))

    def fail(self, client: httpx.Client, league_slug: str, event_id: int, error: str) -> None:
        url = self._site_url(league_slug, f"/api/discord/events/{event_id}/fail")
        resp = client.post(url, headers=self._headers, json={"error": error[:1200]})
        resp.raise_for_status()

    def poll_pending_dms(
        self, client: httpx.Client, league_slug: str, *, site_timeout: float | None = None
    ) -> list[dict[str, Any]]:
        url = self._site_url(league_slug, "/api/discord/dms/pending")
        timeout = site_timeout if site_timeout is not None else self.settings.site_timeout_seconds
        resp = client.get(
            url,
            params={"league_slug": league_slug, "limit": 20},
            headers=self._headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("message") or "pending DM fetch failed")
        return list(data.get("events") or [])

    def ack_dm(
        self,
        client: httpx.Client,
        league_slug: str,
        event_id: int,
        *,
        discord_channel_id: str = "",
        discord_message_id: str = "",
    ) -> None:
        url = self._site_url(league_slug, f"/api/discord/dms/{event_id}/ack")
        resp = client.post(
            url,
            headers=self._headers,
            json={
                "discord_channel_id": str(discord_channel_id or "").strip(),
                "discord_message_id": str(discord_message_id or "").strip(),
            },
        )
        resp.raise_for_status()

    def fail_dm(self, client: httpx.Client, league_slug: str, event_id: int, error: str) -> None:
        url = self._site_url(league_slug, f"/api/discord/dms/{event_id}/fail")
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
        if str(event.get("event_key") or "") == "playoff_bracket_update":
            self._deliver_playoff_bracket_update(
                site_client, discord_client, league_slug, event_id, channel_id, event
            )
            return
        payload = event.get("payload") or {}
        edit_message_id = str(payload.get("edit_message_id") or "").strip()
        if payload.get("post_new_message"):
            edit_message_id = ""
        bodies = format_discord_messages(event, max_parts=self.settings.max_message_parts)
        delay = float(self.settings.delivery_delay_seconds)
        delivered_message_id = ""
        for i, body in enumerate(bodies):
            if i > 0 and delay > 0:
                time.sleep(delay)
            if i == 0 and edit_message_id:
                if not self.discord_message_exists(
                    discord_client, channel_id, edit_message_id
                ):
                    log.warning(
                        "Discord edit target %s not found in channel %s; posting new message",
                        edit_message_id,
                        channel_id,
                    )
                    delivered_message_id = self.post_discord(
                        discord_client, channel_id, body
                    )
                else:
                    try:
                        delivered_message_id = self.patch_discord(
                            discord_client, channel_id, edit_message_id, body
                        )
                    except RuntimeError as exc:
                        err = str(exc)
                        if "404" not in err and "Unknown Message" not in err:
                            raise
                        log.warning(
                            "Discord edit failed for message %s in channel %s; posting new message (%s)",
                            edit_message_id,
                            channel_id,
                            err,
                        )
                        delivered_message_id = self.post_discord(
                            discord_client, channel_id, body
                        )
            else:
                delivered_message_id = self.post_discord(
                    discord_client, channel_id, body
                )
        if not delivered_message_id:
            raise RuntimeError(
                f"Event {event_id} produced no Discord message id after delivery"
            )
        self.ack(
            site_client,
            league_slug,
            event_id,
            discord_message_id=delivered_message_id,
            discord_channel_id=channel_id,
        )

    def _deliver_playoff_bracket_update(
        self,
        site_client: httpx.Client,
        discord_client: httpx.Client,
        league_slug: str,
        event_id: int,
        channel_id: str,
        event: dict[str, Any],
    ) -> None:
        delay = float(self.settings.delivery_delay_seconds)
        payload = event.get("payload") or {}
        post_new_messages = bool(payload.get("post_new_messages"))
        deliveries = format_playoff_bracket_deliveries(event)
        if not deliveries:
            raise RuntimeError(
                f"Event {event_id} has no playoff bracket content to deliver "
                "(projection-only or empty series list)"
            )
        series_results: list[dict[str, str]] = []
        last_message_id = ""
        for i, item in enumerate(deliveries):
            if i > 0 and delay > 0:
                time.sleep(delay)
            pair_key = str(item.get("pair_key") or "").strip()
            edit_message_id = str(item.get("edit_message_id") or "").strip()
            if post_new_messages:
                edit_message_id = ""
            body = sanitize_discord_message_body(
                {k: v for k, v in item.items() if k in {"content", "embeds"}}
            )
            message_id = ""
            if edit_message_id:
                if not self.discord_message_exists(
                    discord_client, channel_id, edit_message_id
                ):
                    log.warning(
                        "Playoff bracket edit target %s not found in channel %s; posting new message",
                        edit_message_id,
                        channel_id,
                    )
                    message_id = self.post_discord(discord_client, channel_id, body)
                else:
                    try:
                        message_id = self.patch_discord(
                            discord_client, channel_id, edit_message_id, body
                        )
                    except RuntimeError as exc:
                        err = str(exc)
                        if "404" not in err and "Unknown Message" not in err:
                            raise
                        log.warning(
                            "Playoff bracket edit failed for message %s; posting new (%s)",
                            edit_message_id,
                            err,
                        )
                        message_id = self.post_discord(discord_client, channel_id, body)
            else:
                message_id = self.post_discord(discord_client, channel_id, body)
            last_message_id = message_id
            if pair_key and message_id:
                series_results.append(
                    {"pair_key": pair_key, "discord_message_id": message_id}
                )
        if not series_results:
            raise RuntimeError(
                f"Event {event_id} produced no playoff bracket Discord posts"
            )
        self.ack(
            site_client,
            league_slug,
            event_id,
            discord_message_id=last_message_id,
            discord_channel_id=channel_id,
            series_deliveries=series_results,
        )

    def deliver_dm(
        self,
        site_client: httpx.Client,
        discord_client: httpx.Client,
        league_slug: str,
        event: dict[str, Any],
    ) -> None:
        event_id = int(event["id"])
        discord_user_id = str(event.get("discord_user_id") or "").strip()
        if not discord_user_id:
            raise RuntimeError(f"DM event {event_id} missing discord_user_id")
        channel_id = self.create_dm_channel(discord_client, discord_user_id)
        if not channel_id:
            raise RuntimeError(f"Could not create DM channel for event {event_id}")
        body = format_direct_message(event)
        message_id = self.post_discord(discord_client, channel_id, body)
        self.ack_dm(
            site_client,
            league_slug,
            event_id,
            discord_channel_id=channel_id,
            discord_message_id=message_id,
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
                dm_events = self.poll_pending_dms(site_client, slug)
                for idx, ev in enumerate(dm_events):
                    if idx > 0 and delay > 0:
                        time.sleep(delay)
                    try:
                        self.deliver_dm(site_client, discord_client, slug, ev)
                        log.info("delivered DM event %s for %s", ev.get("id"), slug)
                    except Exception as exc:
                        last_error = str(exc)
                        log.warning("DM delivery failed event %s %s: %s", ev.get("id"), slug, exc)
                        try:
                            self.fail_dm(site_client, slug, int(ev["id"]), last_error)
                        except Exception:
                            log.exception("DM fail report failed for event %s", ev.get("id"))
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
            "Starting league_discord_bot for leagues: %s (poll=%.1fs, delay=%.1fs, max_parts=%s, site_timeout=%.0fs)",
            ", ".join(sorted(self.settings.league_base_urls)),
            self.settings.poll_seconds,
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
            poll_interval = max(2.0, float(self.settings.poll_seconds))
            while True:
                self.run_cycle(site_client, discord_client)
                time.sleep(poll_interval)
