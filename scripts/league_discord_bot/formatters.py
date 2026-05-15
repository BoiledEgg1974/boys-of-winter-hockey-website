from __future__ import annotations

from typing import Any

from scripts.league_discord_bot.team_maps import format_team_label, team_emoji_prefix


def _preview(text: str, limit: int = 280) -> str:
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def format_discord_message(event: dict[str, Any]) -> dict[str, Any]:
    """Return Discord REST message JSON (content and/or embeds)."""
    league_slug = str(event.get("league_slug") or "")
    event_key = str(event.get("event_key") or "")
    payload = event.get("payload") or {}
    title = str(payload.get("title") or event_key.replace("_", " ").title())
    body = _preview(payload.get("body_preview") or payload.get("message") or payload.get("body") or "")
    url = str(payload.get("url") or "").strip()

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
    return msg
