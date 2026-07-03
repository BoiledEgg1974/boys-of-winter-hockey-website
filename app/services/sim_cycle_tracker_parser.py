"""Parse webhook/bot export posts from #gm-export-tracker."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from scripts.league_discord_bot.team_maps import (
    _CUSTOM_EMOJI_MENTION_RE,
    fhm_team_id_for_abbrev,
    fhm_team_id_for_custom_emoji_mention,
    teams_for_league_slug,
)

_ABBREV_TOKEN_RE = re.compile(r"\b[A-Z]{2,4}\b")
_EXPORT_NOTIFY_RE = re.compile(r"\b(?:has\s+)?export(?:ed|ing|s)?\b", re.IGNORECASE)


def _parse_discord_timestamp(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _message_timestamp(message: dict[str, Any]) -> datetime | None:
    return _parse_discord_timestamp(message.get("timestamp"))


def _message_author_id(message: dict[str, Any]) -> str:
    author = message.get("author") or {}
    return str(author.get("id") or "").strip()


def _message_is_webhook_or_bot(message: dict[str, Any]) -> bool:
    author = message.get("author") or {}
    return bool(author.get("bot"))


def _collect_text_chunks(message: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    content = str(message.get("content") or "").strip()
    if content:
        chunks.append(content)
    for emb in message.get("embeds") or []:
        if not isinstance(emb, dict):
            continue
        for key in ("title", "description"):
            part = str(emb.get(key) or "").strip()
            if part:
                chunks.append(part)
        for field in emb.get("fields") or []:
            if not isinstance(field, dict):
                continue
            for key in ("name", "value"):
                part = str(field.get(key) or "").strip()
                if part:
                    chunks.append(part)
    return chunks


def message_indicates_export(message: dict[str, Any]) -> bool:
    """True when the post looks like a GM export notification (e.g. BOWL-FTP-BOT)."""
    for chunk in _collect_text_chunks(message):
        if _EXPORT_NOTIFY_RE.search(chunk):
            return True
    return False


def _fhm_ids_from_text(league_slug: str, text: str) -> set[int]:
    found: set[int] = set()
    for match in _CUSTOM_EMOJI_MENTION_RE.finditer(str(text or "")):
        mention = match.group(0)
        tid = fhm_team_id_for_custom_emoji_mention(league_slug, mention)
        if tid is not None:
            found.add(int(tid))
    for token in _ABBREV_TOKEN_RE.findall(str(text or "").upper()):
        tid = fhm_team_id_for_abbrev(league_slug, token)
        if tid is not None:
            found.add(int(tid))
    return found


def _message_within_cycle(
    message: dict[str, Any], *, cycle_started_at: datetime | None
) -> bool:
    if cycle_started_at is None:
        return True
    ts = _message_timestamp(message)
    if ts is None:
        return False
    return ts.replace(tzinfo=None) >= cycle_started_at.replace(tzinfo=None)


def tracker_watermark_before_cycle(
    messages: list[dict[str, Any]], *, cycle_started_at: datetime | None
) -> str | None:
    """
    Newest Discord message id strictly before *cycle_started_at*.

    Used to seed ``after`` so the first poll does not re-ingest prior-cycle exports.
    """
    if cycle_started_at is None:
        return None
    anchor = cycle_started_at.replace(tzinfo=None)
    candidates: list[tuple[int, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        mid = str(message.get("id") or "").strip()
        if not mid or not mid.isdigit():
            continue
        ts = _message_timestamp(message)
        if ts is None:
            continue
        if ts.replace(tzinfo=None) < anchor:
            candidates.append((int(mid), mid))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def parse_export_fhm_team_ids_from_messages(
    league_slug: str,
    messages: list[dict[str, Any]],
    *,
    cycle_started_at: datetime | None = None,
    allowed_author_ids: set[str] | None = None,
    require_bot_author: bool = True,
) -> tuple[set[int], str | None]:
    """
    Return (fhm_team_ids, latest_message_id) from tracker channel messages.
    Messages are expected newest-first (Discord API default).
    """
    slug = str(league_slug or "").strip()
    if not slug or not messages:
        return set(), None

    exported: set[int] = set()
    latest_id: str | None = None

    for message in messages:
        if not isinstance(message, dict):
            continue
        mid = str(message.get("id") or "").strip()
        if mid and latest_id is None:
            latest_id = mid

        if require_bot_author and not _message_is_webhook_or_bot(message):
            continue

        author_id = _message_author_id(message)
        if allowed_author_ids and author_id and author_id not in allowed_author_ids:
            continue

        if not _message_within_cycle(message, cycle_started_at=cycle_started_at):
            continue

        if not message_indicates_export(message):
            continue

        for chunk in _collect_text_chunks(message):
            exported.update(_fhm_ids_from_text(slug, chunk))

    return exported, latest_id


def newest_message_id(messages: list[dict[str, Any]]) -> str | None:
    best: tuple[int, str] | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        mid = str(message.get("id") or "").strip()
        if not mid or not mid.isdigit():
            continue
        pair = (int(mid), mid)
        if best is None or pair[0] > best[0]:
            best = pair
    return best[1] if best else None
