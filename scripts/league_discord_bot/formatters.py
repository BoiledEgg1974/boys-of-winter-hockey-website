from __future__ import annotations

import math
from typing import Any

from scripts.league_discord_bot.team_maps import format_team_label, team_emoji_prefix

DISCORD_SITE_MORE_FOOTER = (
    "For more news, stats and more, go to https://www.bowlhockey.com"
)

ARTICLE_TEXT_DISCORD_EVENT_KEYS = frozenset(
    {
        "news_published",
        "gm_news_published",
        "admin_news_published",
        "announcement_posted",
        "story_published",
        "ap_redemption_posted",
    }
)

ALWAYS_TEXT_ONLY_DISCORD_EVENT_KEYS = frozenset(
    {
        "trade_request",
        "staff_transaction_posted",
        "draft_hub_pick_made",
        "draft_hub_on_clock",
        "draft_hub_on_deck",
        "draft_hub_completed",
        "expansion_draft_pick_made",
    }
)


def _discord_embed_url(url: str) -> str:
    """Discord embed links must be absolute http(s); site may queue relative paths if unset."""
    u = str(url or "").strip()
    if u.lower().startswith(("http://", "https://")):
        return u
    return ""


def sanitize_discord_message_body(body: dict[str, Any]) -> dict[str, Any]:
    """Last-line cleanup before Discord REST POST (strips invalid embed URLs)."""
    out: dict[str, Any] = {}
    content = str(body.get("content") or "").strip()
    if content:
        out["content"] = content
    clean_embeds: list[dict[str, Any]] = []
    for emb in body.get("embeds") or []:
        if not isinstance(emb, dict):
            continue
        e = {k: v for k, v in emb.items() if v is not None and v != ""}
        link = _discord_embed_url(str(e.pop("url", "") or ""))
        if link:
            e["url"] = link
        if e:
            clean_embeds.append(e)
    if clean_embeds:
        out["embeds"] = clean_embeds
    return out or {"content": str(body.get("content") or "Notification")}


def format_direct_message(event: dict[str, Any]) -> dict[str, Any]:
    """Assistant-style private DM alert for a recipient-specific site notification."""
    payload = event.get("payload") or {}
    name = str(payload.get("discord_name") or "there").strip()
    title = _preview(str(payload.get("title") or "New site notification"), 180)
    preview = _preview(str(payload.get("preview") or ""), 260)
    url = str(payload.get("url") or "").strip()
    league = str(payload.get("league_slug") or event.get("league_slug") or "your league").strip()
    lines = [
        f"Hi {name}, your BOWL assistant here.",
        f"You have a new {league} message waiting in GM Messages.",
        f"**{title}**",
    ]
    if preview:
        lines.append(f"> {preview}")
    if url:
        lines.append(f"Open it here: {url}")
    return sanitize_discord_message_body({"content": "\n".join(lines)})


def _preview(text: str, limit: int = 280) -> str:
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


DISCORD_MAX_CONTENT_LEN = 2000
DISCORD_MAX_EMBED_DESC_LEN = 4096


def _payload_has_image(payload: dict[str, Any]) -> bool:
    flag = payload.get("has_image")
    if flag is True or str(flag).lower() in {"1", "true", "yes"}:
        return True
    for key in ("image_url", "image_rel_path", "thumbnail_url"):
        if str(payload.get(key) or "").strip():
            return True
    return False


def _body_text(payload: dict[str, Any], *, full: bool = False) -> str:
    if full:
        raw = payload.get("body") or payload.get("body_preview") or payload.get("message") or ""
    else:
        raw = payload.get("body_preview") or payload.get("message") or payload.get("body") or ""
    return str(raw or "").strip()


def _is_text_only_discord_post(event_key: str, payload: dict[str, Any]) -> bool:
    if event_key in ALWAYS_TEXT_ONLY_DISCORD_EVENT_KEYS:
        return True
    if _payload_has_image(payload):
        return False
    if event_key not in ARTICLE_TEXT_DISCORD_EVENT_KEYS:
        return False
    return bool(_body_text(payload, full=True))


def _text_only_header_lines(
    league_slug: str,
    event_key: str,
    payload: dict[str, Any],
    *,
    title: str,
) -> list[str]:
    lines: list[str] = []
    if event_key in ("news_published", "gm_news_published", "admin_news_published"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        lines.append(f"**{title}**")
    elif event_key == "announcement_posted":
        lines.append(f"**{title}**")
        level = str(payload.get("level") or "").strip()
        if level:
            lines.append(f"Level: {level}")
    elif event_key == "ap_redemption_posted":
        gm_name = str(payload.get("gm_name") or "").strip()
        lines.append("**AP redemption approved**")
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        if gm_name:
            lines.append(f"GM: {gm_name}")
        lines.append(f"**{title}**")
    elif event_key == "story_published":
        lines.append(f"**{title}**")
    elif event_key == "draft_hub_on_clock":
        prefix = team_emoji_prefix(league_slug, payload)
        team_name = str(payload.get("team_name") or "").strip()
        rnd = payload.get("round")
        sel = payload.get("selection")
        mentions = str(payload.get("gm_mentions") or "GM").strip()
        mins = payload.get("timer_minutes") or 2
        lines.append(f"On the clock: {prefix}{team_name}".strip())
        lines.append(f"Round {rnd}, Selection {sel}")
        lines.append(f"**{mentions}, you have {mins} minute{'s' if int(mins) != 1 else ''}!**")
    elif event_key == "draft_hub_on_deck":
        prefix = team_emoji_prefix(league_slug, payload)
        team_name = str(payload.get("team_name") or "").strip()
        rnd = payload.get("round")
        sel = payload.get("selection")
        mentions = str(payload.get("gm_mentions") or "GM").strip()
        lines.append(f"On deck: {prefix}{team_name}".strip())
        lines.append(f"Round {rnd}, Selection {sel}")
        lines.append(f"{mentions}, get ready!")
    elif event_key == "draft_hub_completed":
        dname = str(payload.get("draft_name") or "Draft Hub")
        pick_count = payload.get("pick_count")
        lines.append(f"**{dname} complete**")
        if pick_count is not None:
            lines.append(f"{pick_count} pick(s) recorded.")
        recap = payload.get("recap_lines") or []
        if isinstance(recap, list) and recap:
            lines.append("")
            lines.append("Highlights:")
            for row in recap[:8]:
                lines.append(f"· {row}")
        archive_url = str(payload.get("archive_url") or "").strip()
        if archive_url:
            lines.append("")
            lines.append(f"Archive: {archive_url}")
    elif event_key == "draft_hub_pick_made":
        dname = str(payload.get("draft_name") or "Draft Hub")
        rnd = payload.get("round")
        ov = payload.get("overall_pick")
        player = str(payload.get("player_name") or "")
        pos = str(payload.get("player_pos") or "").strip()
        prefix = team_emoji_prefix(league_slug, payload)
        lines.append(f"{prefix}**{dname}** — pick".strip())
        ply = player + (f" ({pos})" if pos else "")
        lines.append(f"R{rnd} • Overall #{ov} • {ply}")
    elif event_key == "expansion_draft_pick_made":
        dname = str(payload.get("draft_name") or "Expansion draft")
        phase = str(payload.get("phase") or "").strip()
        rnd = payload.get("round")
        ov = payload.get("overall_pick")
        player = str(payload.get("player_name") or "")
        prefix = team_emoji_prefix(league_slug, payload)
        ph_part = f" [{phase}]" if phase else ""
        lines.append(f"{prefix}**{dname}**{ph_part} — pick".strip())
        lines.append(f"R{rnd} • Overall #{ov} • **{player}**")
    elif event_key == "staff_transaction_posted":
        action = str(payload.get("action") or "").strip().lower()
        head = "**Staff hired**" if action == "hired" else "**Staff fired**"
        lines.append(head)
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
    elif event_key == "trade_request":
        prefix = team_emoji_prefix(league_slug, payload)
        lines.append(
            f"{prefix}**Trade / ops update** (#{payload.get('request_id', '')})".strip()
        )
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        req_type = str(payload.get("request_type") or "").strip()
        status = str(payload.get("status") or "").strip()
        if req_type:
            lines.append(f"Type: {req_type}")
        if status:
            lines.append(f"Status: **{status}**")
        lines.append(f"**{title}**")
    elif event_key == "bowl_six_leaders_update":
        lines.append(f"**{title}**")
        status = str(payload.get("slate_status") or "").strip()
        if status:
            lines.append(f"Slate: **{status}**")
    elif event_key in ("trade_market_selling_posted", "trade_market_buying_posted"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        lines.append(f"**{title}**")
    else:
        lines.append(f"**{title}**")
    return lines


def _text_only_body_text(
    league_slug: str,
    event_key: str,
    payload: dict[str, Any],
) -> str:
    body = _body_text(payload, full=True)
    if event_key == "trade_request":
        note = str(payload.get("admin_note") or "").strip()
        if note and note not in body:
            body = f"{body}\n\nAdmin note: {note}".strip() if body else f"Admin note: {note}"
    if event_key in ("draft_hub_pick_made", "expansion_draft_pick_made"):
        src = str(payload.get("pick_source") or "").strip()
        if src and src not in body:
            body = f"{body} · `{src}`" if body else f"Source: `{src}`"
    if event_key in ("draft_hub_on_clock", "draft_hub_on_deck"):
        url = str(payload.get("url") or "").strip()
        if url and url not in body:
            body = f"{body}\n{url}".strip() if body else url
    return body


def _chunk_text(text: str, limit: int, max_parts: int) -> list[str]:
    rest = str(text or "").strip()
    if not rest:
        return []
    if len(rest) <= limit:
        return [rest]
    parts: list[str] = []
    while rest and len(parts) < max_parts:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest and parts:
        tail = parts[-1]
        if len(tail) >= limit - 1:
            parts[-1] = tail[: limit - 1].rstrip() + "…"
        elif len(parts) >= max_parts:
            parts[-1] = (tail + " …").strip()[:limit]
    return parts


def _split_content_with_footer(
    text: str,
    *,
    footer: str,
    max_parts: int,
) -> list[str]:
    """Split *text* across messages; *footer* appears only on the final part."""
    footer_block = f"\n\n{footer}"
    if not text.strip():
        return [footer] if len(footer) <= DISCORD_MAX_CONTENT_LEN else [footer[: DISCORD_MAX_CONTENT_LEN - 1] + "…"]
    if len(text) + len(footer_block) <= DISCORD_MAX_CONTENT_LEN:
        return [text + footer_block]
    parts: list[str] = []
    rest = text
    while rest and len(parts) < max_parts:
        is_last = len(parts) >= max_parts - 1
        limit = DISCORD_MAX_CONTENT_LEN - (len(footer_block) if is_last else 0)
        if len(rest) <= limit:
            chunk = rest
            rest = ""
        else:
            cut = rest.rfind("\n\n", 0, limit)
            if cut < limit // 3:
                cut = rest.rfind("\n", 0, limit)
            if cut < limit // 3:
                cut = rest.rfind(" ", 0, limit)
            if cut < limit // 3:
                cut = limit
            chunk = rest[:cut].rstrip()
            rest = rest[cut:].lstrip()
        if is_last:
            chunk = (chunk + footer_block)[:DISCORD_MAX_CONTENT_LEN]
        parts.append(chunk)
    if rest and parts:
        tail = parts[-1]
        suffix = f"\n\n…\n\n{footer}" if footer not in tail else ""
        parts[-1] = (tail.rstrip() + suffix)[:DISCORD_MAX_CONTENT_LEN]
    return parts


def _parts_needed_for_text(text: str, *, max_parts: int) -> int:
    footer_block = f"\n\n{DISCORD_SITE_MORE_FOOTER}"
    if len(text) + len(footer_block) <= DISCORD_MAX_CONTENT_LEN:
        return 1
    needed = math.ceil(len(text) / max(400, DISCORD_MAX_CONTENT_LEN - len(footer_block)))
    return min(4, max(1, max(max_parts, needed)))


def _build_full_text_messages(
    lines_prefix: list[str],
    body: str,
    *,
    max_parts: int,
) -> list[dict[str, Any]]:
    prefix = "\n".join([ln for ln in lines_prefix if ln])
    main = f"{prefix}\n\n{body}".strip() if prefix and body else (prefix or body)
    chunks = _split_content_with_footer(
        main,
        footer=DISCORD_SITE_MORE_FOOTER,
        max_parts=max_parts,
    )
    return [{"content": chunk} for chunk in chunks if chunk]


def _trade_market_embed(payload: dict[str, Any], *, title: str, body: str, url: str) -> dict[str, Any]:
    team_name = str(payload.get("team_name") or "").strip()
    team_abbrev = str(payload.get("team_abbrev") or "").strip()
    team_logo_url = _discord_embed_url(str(payload.get("team_logo_url") or ""))
    embed: dict[str, Any] = {
        "title": title[:256],
        "description": body[:DISCORD_MAX_EMBED_DESC_LEN] if body else None,
    }
    if url:
        embed["url"] = url
    if team_name:
        author_name = team_name
        if team_abbrev and team_abbrev not in team_name:
            author_name = f"{team_name} ({team_abbrev})"
        embed["author"] = {"name": author_name[:256]}
        if team_logo_url:
            embed["author"]["icon_url"] = team_logo_url
    if team_logo_url:
        embed["thumbnail"] = {"url": team_logo_url}
    return {k: v for k, v in embed.items() if v}


def _news_embed(league_slug: str, payload: dict[str, Any], *, title: str, body: str, url: str) -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": title[:256],
        "description": body[:DISCORD_MAX_EMBED_DESC_LEN] if body else None,
    }
    if url:
        embed["url"] = url
    team_label = format_team_label(league_slug, payload)
    if team_label:
        embed["author"] = {"name": team_label[:256]}
    image_url = _discord_embed_url(
        str(payload.get("image_url") or payload.get("thumbnail_url") or "")
    )
    if image_url:
        embed["image"] = {"url": image_url}
    return {k: v for k, v in embed.items() if v}


def _split_message_bodies(msg: dict[str, Any], *, max_parts: int) -> list[dict[str, Any]]:
    """Split one Discord payload into up to *max_parts* messages under API limits."""
    content = str(msg.get("content") or "")
    embeds = list(msg.get("embeds") or [])
    if embeds:
        emb = dict(embeds[0])
        desc = str(emb.get("description") or "")
        if len(desc) > DISCORD_MAX_EMBED_DESC_LEN:
            emb["description"] = desc[: DISCORD_MAX_EMBED_DESC_LEN - 1].rstrip() + "…"
        link = _discord_embed_url(str(emb.get("url") or ""))
        if link:
            emb["url"] = link
        else:
            emb.pop("url", None)
        embeds = [emb]

    if len(content) <= DISCORD_MAX_CONTENT_LEN:
        out = dict(msg)
        if embeds:
            out["embeds"] = embeds
        return [out]

    chunks = _chunk_text(content, DISCORD_MAX_CONTENT_LEN, max_parts)
    if not chunks:
        return [msg]
    bodies: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        body: dict[str, Any] = {"content": chunk}
        if i == 0 and embeds:
            body["embeds"] = embeds
        bodies.append(body)
    return bodies


def format_discord_message(event: dict[str, Any]) -> dict[str, Any]:
    """Return a single Discord REST message JSON (first part if split would apply)."""
    return format_discord_messages(event, max_parts=1)[0]


def format_discord_messages(event: dict[str, Any], *, max_parts: int = 2) -> list[dict[str, Any]]:
    """Return one or more Discord REST message bodies (split when over content limit)."""
    league_slug = str(event.get("league_slug") or "")
    event_key = str(event.get("event_key") or "")
    payload = event.get("payload") or {}
    title = str(payload.get("title") or event_key.replace("_", " ").title())
    body_short = _preview(
        payload.get("body_preview") or payload.get("message") or payload.get("body") or ""
    )
    url = _discord_embed_url(str(payload.get("url") or ""))

    if event_key == "bowl_six_leaders_update":
        body_full = _body_text(payload, full=True) or body_short
        embed: dict[str, Any] = {
            "title": title[:256],
            "description": body_full[:DISCORD_MAX_EMBED_DESC_LEN] if body_full else None,
        }
        if url:
            embed["url"] = url
        embed = {k: v for k, v in embed.items() if v}
        if embed.get("description") or embed.get("url"):
            return _split_message_bodies({"embeds": [embed]}, max_parts=max(1, int(max_parts)))
        return [{"content": f"**{title}**"}]

    if event_key in ("trade_market_selling_posted", "trade_market_buying_posted"):
        body_full = _body_text(payload, full=True) or body_short
        embed = _trade_market_embed(payload, title=title, body=body_full, url=url)
        if embed.get("description") or embed.get("url"):
            return _split_message_bodies({"embeds": [embed]}, max_parts=max(1, int(max_parts)))
        return [{"content": f"**{title}**"}]

    if event_key in ("news_published", "gm_news_published", "admin_news_published") and _payload_has_image(payload):
        body_full = _body_text(payload, full=True) or body_short
        embed = _news_embed(league_slug, payload, title=title, body=body_full, url=url)
        if embed.get("description") or embed.get("url"):
            return _split_message_bodies({"embeds": [embed]}, max_parts=max(1, int(max_parts)))
        return [{"content": f"**{title}**"}]

    if _is_text_only_discord_post(event_key, payload):
        lines = _text_only_header_lines(league_slug, event_key, payload, title=title)
        body_full = _text_only_body_text(league_slug, event_key, payload)
        effective_parts = _parts_needed_for_text(
            "\n".join(lines) + "\n\n" + body_full,
            max_parts=max(1, int(max_parts)),
        )
        return _build_full_text_messages(lines, body_full, max_parts=effective_parts)

    lines: list[str] = []
    if event_key in ("news_published", "gm_news_published", "admin_news_published"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        lines.append(f"**{title}**")
        if body_short:
            lines.append(body_short)
    elif event_key == "draft_hub_pick_made":
        dname = str(payload.get("draft_name") or "Draft Hub")
        rnd = payload.get("round")
        ov = payload.get("overall_pick")
        player = str(payload.get("player_name") or "")
        pos = str(payload.get("player_pos") or "").strip()
        src = str(payload.get("pick_source") or "").strip()
        prefix = team_emoji_prefix(league_slug, payload)
        head = f"{prefix}**{dname}** — pick".strip()
        lines.append(head)
        ply = player + (f" ({pos})" if pos else "")
        lines.append(f"R{rnd} • Overall #{ov} • {ply}" + (f" · `{src}`" if src else ""))
        if body_short:
            lines.append(body_short)
    elif event_key == "expansion_draft_pick_made":
        dname = str(payload.get("draft_name") or "Expansion draft")
        phase = str(payload.get("phase") or "").strip()
        rnd = payload.get("round")
        ov = payload.get("overall_pick")
        player = str(payload.get("player_name") or "")
        src = str(payload.get("pick_source") or "").strip()
        prefix = team_emoji_prefix(league_slug, payload)
        ph_part = f" [{phase}]" if phase else ""
        lines.append(f"{prefix}**{dname}**{ph_part} — pick".strip())
        lines.append(f"R{rnd} • Overall #{ov} • **{player}**" + (f" · `{src}`" if src else ""))
        if body_short:
            lines.append(body_short)
    elif event_key == "staff_transaction_posted":
        action = str(payload.get("action") or "").strip().lower()
        staff_name = str(payload.get("staff_name") or "").strip()
        role_label = str(payload.get("role_label") or "").strip()
        gm_name = str(payload.get("gm_name") or "").strip()
        head = "**Staff hired**" if action == "hired" else "**Staff fired**"
        lines.append(head)
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        if staff_name:
            line = staff_name
            if role_label:
                line += f" ({role_label})"
            lines.append(line)
        if gm_name:
            lines.append(f"GM: {gm_name}")
    elif event_key == "ap_redemption_posted":
        label = str(payload.get("redemption_label") or "").strip()
        gm_name = str(payload.get("gm_name") or "").strip()
        cost = payload.get("total_cost")
        lines.append("**AP redemption approved**")
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        if gm_name:
            lines.append(f"GM: {gm_name}")
        if label:
            lines.append(label)
        if cost is not None:
            lines.append(f"AP deducted: **{cost}**")
    elif event_key == "trade_request":
        req_type = str(payload.get("request_type") or "").strip()
        status = str(payload.get("status") or "").strip()
        note = str(payload.get("admin_note") or "").strip()
        prefix = team_emoji_prefix(league_slug, payload)
        lines.append(f"{prefix}**Trade / ops update** (#{payload.get('request_id', '')})".strip())
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        if req_type:
            lines.append(f"Type: {req_type}")
        if status:
            lines.append(f"Status: **{status}**")
        if note:
            lines.append(_preview(note, 200))
    elif event_key == "announcement_posted":
        level = str(payload.get("level") or "").strip()
        lines.append(f"**{title}**")
        if level:
            lines.append(f"Level: {level}")
        if body_short:
            lines.append(body_short)
    else:
        lines.append(f"**{title}**")
        if body_short:
            lines.append(body_short)

    content = "\n".join([ln for ln in lines if ln])
    embed: dict[str, Any] = {"title": title[:256], "description": body_short[:4096] if body_short else None}
    if url:
        embed["url"] = url
    embed = {k: v for k, v in embed.items() if v}
    msg: dict[str, Any] = {}
    if content:
        msg["content"] = content[:2000]
    if embed.get("description") or embed.get("url"):
        msg["embeds"] = [embed]
    if not msg:
        msg["content"] = f"Event `{event_key}`"
    return _split_message_bodies(msg, max_parts=max(1, int(max_parts)))
