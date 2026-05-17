from __future__ import annotations

from typing import Any

from scripts.league_discord_bot.team_maps import format_team_label, team_emoji_prefix


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


def _preview(text: str, limit: int = 280) -> str:
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


DISCORD_MAX_CONTENT_LEN = 2000
DISCORD_MAX_EMBED_DESC_LEN = 4096


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
    body = _preview(payload.get("body_preview") or payload.get("message") or payload.get("body") or "")
    url = _discord_embed_url(str(payload.get("url") or ""))

    lines: list[str] = []
    if event_key in ("news_published", "gm_news_published", "admin_news_published"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        lines.append(f"**{title}**")
        if body:
            lines.append(body)
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
        if body:
            lines.append(body)
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
        if body:
            lines.append(body)
    elif event_key == "staff_transaction_posted":
        action = str(payload.get("action") or "").strip().lower()
        staff_name = str(payload.get("staff_name") or "").strip()
        role_label = str(payload.get("role_label") or "").strip()
        gm_email = str(payload.get("gm_email") or "").strip()
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
        if gm_email:
            lines.append(f"GM: {gm_email}")
    elif event_key == "ap_redemption_posted":
        label = str(payload.get("redemption_label") or "").strip()
        cost = payload.get("total_cost")
        lines.append("**AP redemption approved**")
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
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
        if body:
            lines.append(body)
    else:
        lines.append(f"**{title}**")
        if body:
            lines.append(body)

    content = "\n".join([ln for ln in lines if ln])
    embed: dict[str, Any] = {"title": title[:256], "description": body[:4096] if body else None}
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
