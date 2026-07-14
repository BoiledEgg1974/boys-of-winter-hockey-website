from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from scripts.league_discord_bot.team_maps import (
    emoji_for_abbrev,
    entry_for_fhm_team_id,
    export_status_emoji,
    format_team_label,
    league_logo_emoji,
    sim_cycle_embed_color,
    team_emoji_prefix,
)

DISCORD_SITE_MORE_FOOTER = (
    "For more news, stats and more, go to https://www.bowlhockey.com"
)

ET = ZoneInfo("America/New_York")

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
        "confirmed_trade",
        "trade_request",
        "staff_transaction_posted",
        "draft_hub_pick_made",
        "draft_hub_on_clock",
        "draft_hub_on_deck",
        "draft_hub_completed",
        "expansion_draft_pick_made",
        "expansion_draft_on_clock",
        "expansion_draft_completed",
        "bowl_six_rosters_unlocked",
        "bowl_six_lock_warning",
        "playoff_predictions",
        "playoff_bracket_update",
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


def _team_gm_mention_line(payload: dict[str, Any]) -> str:
    mention = str(payload.get("team_gm_mention") or "").strip()
    return mention if mention.startswith("<@") and mention.endswith(">") else ""


def _gm_mentions_line(payload: dict[str, Any]) -> str:
    mentions = str(payload.get("gm_mentions") or "").strip()
    if not mentions:
        return ""
    parts = mentions.split()
    if not parts:
        return ""
    valid = [p for p in parts if p.startswith("<@") and p.endswith(">")]
    return " ".join(valid) if valid else ""


def _append_team_gm_mention(lines: list[str], payload: dict[str, Any]) -> None:
    mention = _team_gm_mention_line(payload)
    if mention and mention not in lines:
        lines.append(mention)


def _append_gm_mentions(lines: list[str], payload: dict[str, Any]) -> None:
    mention = _gm_mentions_line(payload) or _team_gm_mention_line(payload)
    if mention and mention not in lines:
        lines.append(mention)


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
    if event_key in ("news_published", "gm_news_published", "admin_news_published", "confirmed_trade"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
        if event_key == "confirmed_trade":
            _append_gm_mentions(lines, payload)
        elif team_line or payload.get("league_wide"):
            _append_team_gm_mention(lines, payload)
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
            _append_team_gm_mention(lines, payload)
        if gm_name:
            lines.append(f"GM: {gm_name}")
        lines.append(f"**{title}**")
    elif event_key == "story_published":
        mention = _team_gm_mention_line(payload)
        if mention:
            lines.append(mention)
        lines.append(f"**{title}**")
    elif event_key == "draft_hub_on_clock":
        prefix = team_emoji_prefix(league_slug, payload)
        team_name = str(payload.get("team_name") or "").strip()
        rnd = payload.get("round")
        sel = payload.get("selection")
        mentions = str(payload.get("gm_mentions") or "GM").strip()
        lines.append(f"On the clock: {prefix}{team_name}".strip())
        lines.append(f"Round {rnd}, Selection {sel}")
        lines.append(f"**{mentions}, make your selection with /draft when ready.**")
    elif event_key == "draft_hub_on_deck":
        prefix = team_emoji_prefix(league_slug, payload)
        team_name = str(payload.get("team_name") or "").strip()
        rnd = payload.get("round")
        sel = payload.get("selection")
        mentions = str(payload.get("gm_mentions") or "GM").strip()
        lines.append(f"On deck: {prefix}{team_name}".strip())
        lines.append(f"Round {rnd}, Selection {sel}")
        lines.append(f"{mentions}, get ready!")
    elif event_key == "expansion_draft_on_clock":
        prefix = team_emoji_prefix(league_slug, payload)
        team_name = str(payload.get("team_name") or "").strip()
        rnd = payload.get("round")
        phase = str(payload.get("phase") or "").strip()
        ov = payload.get("overall_pick")
        lines.append(f"On the clock: {prefix}{team_name}".strip())
        _append_team_gm_mention(lines, payload)
        phase_bit = f" · {phase} phase" if phase else ""
        lines.append(f"Round {rnd}{phase_bit} · Overall #{ov}")
        lines.append("**Commissioner: record the pick with /expansionpick when ready.**")
    elif event_key == "expansion_draft_completed":
        dname = str(payload.get("draft_name") or "Expansion draft")
        pick_count = payload.get("pick_count")
        ended_early = bool(payload.get("ended_early"))
        suffix = " ended early" if ended_early else " complete"
        lines.append(f"**{dname}{suffix}**")
        if pick_count is not None:
            lines.append(f"{pick_count} pick(s) recorded.")
        recap = payload.get("recap_lines") or []
        if isinstance(recap, list) and recap:
            lines.append("")
            lines.append("Highlights:")
            for row in recap[:8]:
                lines.append(f"· {row}")
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
        _append_team_gm_mention(lines, payload)
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
        _append_team_gm_mention(lines, payload)
        lines.append(f"R{rnd} • Overall #{ov} • **{player}**")
    elif event_key == "staff_transaction_posted":
        action = str(payload.get("action") or "").strip().lower()
        head = "**Staff hired**" if action == "hired" else "**Staff fired**"
        lines.append(head)
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
            _append_team_gm_mention(lines, payload)
    elif event_key == "playoff_predictions":
        lines.append(f"**{title}**")
    elif event_key == "playoff_bracket_update":
        lines.append(f"**{title}**")
    elif event_key == "trade_request":
        prefix = team_emoji_prefix(league_slug, payload)
        lines.append(
            f"{prefix}**Trade / ops update** (#{payload.get('request_id', '')})".strip()
        )
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
            _append_team_gm_mention(lines, payload)
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
    elif event_key == "sim_cycle_update":
        lines.append(f"**{title}**")
    elif event_key in ("bowl_six_rosters_unlocked", "bowl_six_lock_warning"):
        lines.append(f"**{title}**")
        lock_display = str(payload.get("lock_display") or "").strip()
        if lock_display:
            lines.append(f"Lock: {lock_display}")
    elif event_key in ("trade_market_selling_posted", "trade_market_buying_posted"):
        team_line = format_team_label(league_slug, payload)
        if team_line:
            lines.append(team_line)
            _append_team_gm_mention(lines, payload)
        lines.append(f"**{title}**")
    else:
        lines.append(f"**{title}**")
    return lines


def _playoff_team_prefix(league_slug: str, team: dict[str, Any]) -> str:
    entry = entry_for_fhm_team_id(league_slug, team.get("fhm_team_id"))
    if entry and entry[1]:
        return f"{entry[1]} "
    emoji = emoji_for_abbrev(league_slug, str(team.get("abbrev") or ""))
    return f"{emoji} " if emoji else ""


def _format_playoff_matchup_block(
    league_slug: str,
    row: dict[str, Any],
    *,
    series_index: int,
) -> str:
    ta = row.get("team_a") or {}
    tb = row.get("team_b") or {}
    round_label = str(row.get("round_label") or "Series").strip()
    ab_a = str(ta.get("abbrev") or ta.get("name") or "A").strip()
    ab_b = str(tb.get("abbrev") or tb.get("name") or "B").strip()
    score = str(row.get("series_score") or "").strip()
    header = f"**{round_label} · Series {series_index}**"
    if score and score not in {"0-0", "0–0"}:
        header += f" ({score})"
    matchup = (
        f"{_playoff_team_prefix(league_slug, ta)}**{ab_a}** vs. "
        f"{_playoff_team_prefix(league_slug, tb)}**{ab_b}**"
    )
    lines = [
        header,
        matchup,
        str(row.get("team_a_stats") or "").strip(),
        str(row.get("team_b_stats") or "").strip(),
        f"Prediction: {str(row.get('prediction_line') or '—').strip()}",
        f"Regular-season H2H: {str(row.get('h2h_line') or '—').strip()}",
    ]
    return "\n".join(ln for ln in lines if ln)


def _format_playoff_predictions_body(league_slug: str, payload: dict[str, Any]) -> str:
    series = payload.get("series") or []
    if not isinstance(series, list) or not series:
        return str(payload.get("body") or "").strip()
    blocks: list[str] = []
    for idx, row in enumerate(series, start=1):
        if not isinstance(row, dict):
            continue
        blocks.append(_format_playoff_matchup_block(league_slug, row, series_index=idx))
    note = str(payload.get("prediction_method_note") or "").strip()
    if note:
        blocks.append(f"_{note}_")
    return "\n\n".join(blocks)


def _format_playoff_predictions_messages(
    league_slug: str,
    payload: dict[str, Any],
    *,
    title: str,
    max_parts: int,
) -> list[dict[str, Any]]:
    """One Discord message per playoff matchup so users can react to each series."""
    series = payload.get("series") or []
    if not isinstance(series, list) or not series:
        body = str(payload.get("body") or "").strip()
        return _build_full_text_messages([f"**{title}**"], body, max_parts=max(1, int(max_parts)))

    rows = [(idx, row) for idx, row in enumerate(series, start=1) if isinstance(row, dict)]
    if not rows:
        return [{"content": f"**{title}**"}]

    note = str(payload.get("prediction_method_note") or "").strip()
    messages: list[dict[str, Any]] = []
    for i, (idx, row) in enumerate(rows):
        block = _format_playoff_matchup_block(league_slug, row, series_index=idx)
        is_first = i == 0
        is_last = i == len(rows) - 1
        prefix_lines = [f"**{title}**"] if is_first else []
        body = block
        if is_last and note:
            body = f"{block}\n\n_{note}_"
        if is_last:
            messages.extend(
                _build_full_text_messages(prefix_lines, body, max_parts=max(1, int(max_parts)))
            )
        else:
            prefix = "\n".join(prefix_lines)
            content = f"{prefix}\n\n{body}".strip() if prefix else body
            messages.append({"content": content[:DISCORD_MAX_CONTENT_LEN]})
    return messages


def _format_playoff_bracket_series_block(
    league_slug: str,
    row: dict[str, Any],
    *,
    series_index: int,
) -> str:
    ta = row.get("team_a") or {}
    tb = row.get("team_b") or {}
    round_label = str(row.get("round_label") or "Series").strip()
    ab_a = str(ta.get("abbrev") or ta.get("name") or "A").strip()
    ab_b = str(tb.get("abbrev") or tb.get("name") or "B").strip()
    score = str(row.get("series_score") or "").strip()
    header = f"**{round_label} · Series {series_index}**"
    if score and score not in {"0-0", "0–0"}:
        header += f" ({score})"
    matchup = (
        f"{_playoff_team_prefix(league_slug, ta)}**{ab_a}** vs. "
        f"{_playoff_team_prefix(league_slug, tb)}**{ab_b}**"
    )
    lines = [header, matchup]
    status = str(row.get("status_line") or "").strip()
    if status:
        lines.append(status)
    return "\n".join(ln for ln in lines if ln)


def format_playoff_bracket_deliveries(event: dict[str, Any]) -> list[dict[str, Any]]:
    """One Discord delivery per bracket series, with edit targets for live updates."""
    league_slug = str(event.get("league_slug") or "")
    payload = event.get("payload") or {}
    if str(payload.get("projection_note") or "").strip():
        return []
    title = str(payload.get("title") or "Playoff bracket")
    series = payload.get("series") or []
    if not isinstance(series, list) or not series:
        return [{"content": f"**{title}**", "pair_key": "", "edit_message_id": ""}]

    note = str(payload.get("projection_note") or "").strip()
    url = _discord_embed_url(str(payload.get("url") or ""))
    deliveries: list[dict[str, Any]] = []
    rows = [row for row in series if isinstance(row, dict)]
    for i, row in enumerate(rows):
        idx = int(row.get("series_index") or i + 1)
        block = _format_playoff_bracket_series_block(league_slug, row, series_index=idx)
        is_first = i == 0
        is_last = i == len(rows) - 1
        prefix_lines = [f"**{title}**"] if is_first else []
        body = block
        if is_last:
            if note:
                body = f"{block}\n\n_{note}_"
            if url:
                body = f"{body}\n{url}".strip()
        prefix = "\n".join(prefix_lines)
        content = f"{prefix}\n\n{body}".strip() if prefix else body
        deliveries.append(
            {
                "content": content[:DISCORD_MAX_CONTENT_LEN],
                "pair_key": str(row.get("pair_key") or "").strip(),
                "edit_message_id": str(row.get("edit_message_id") or "").strip(),
            }
        )
    return deliveries


def _format_playoff_bracket_messages(
    league_slug: str,
    payload: dict[str, Any],
    *,
    title: str,
    max_parts: int,
) -> list[dict[str, Any]]:
    event = {"league_slug": league_slug, "payload": payload}
    return [
        {k: v for k, v in item.items() if k in {"content", "embeds"}}
        for item in format_playoff_bracket_deliveries(event)
    ]


def _text_only_body_text(
    league_slug: str,
    event_key: str,
    payload: dict[str, Any],
) -> str:
    if event_key == "playoff_predictions":
        return _format_playoff_predictions_body(league_slug, payload)
    if event_key == "playoff_bracket_update":
        blocks: list[str] = []
        for idx, row in enumerate(payload.get("series") or [], start=1):
            if isinstance(row, dict):
                blocks.append(
                    _format_playoff_bracket_series_block(
                        league_slug, row, series_index=int(row.get("series_index") or idx)
                    )
                )
        note = str(payload.get("projection_note") or "").strip()
        if note:
            blocks.append(f"_{note}_")
        return "\n\n".join(blocks)
    body = _body_text(payload, full=True)
    if event_key == "trade_request":
        note = str(payload.get("admin_note") or "").strip()
        if note and note not in body:
            body = f"{body}\n\nAdmin note: {note}".strip() if body else f"Admin note: {note}"
    if event_key in ("draft_hub_pick_made", "expansion_draft_pick_made"):
        src = str(payload.get("pick_source") or "").strip()
        if src and src not in body:
            body = f"{body} · `{src}`" if body else f"Source: `{src}`"
    if event_key in ("draft_hub_on_clock", "draft_hub_on_deck", "expansion_draft_on_clock"):
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


def _team_emote_for_fhm_id(league_slug: str, fhm_team_id: int) -> str:
    entry = entry_for_fhm_team_id(league_slug, fhm_team_id)
    if entry:
        return str(entry[1] or "").strip()
    return ""


def _sim_cycle_footer_timestamp(last_raw: str, *, phase: str) -> str:
    if not last_raw:
        return ""
    try:
        dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if phase == "closed":
            dt = dt.astimezone(ET)
        return dt.strftime("%b %d %H:%M")
    except ValueError:
        return last_raw[:16]


def _sim_cycle_team_fhm_ids(payload: dict[str, Any]) -> tuple[list[int], list[int]]:
    """Exported and pending FHM team ids from payload (flat lists or legacy divisions)."""
    exported_raw = payload.get("exported")
    pending_raw = payload.get("pending")
    if isinstance(exported_raw, list) or isinstance(pending_raw, list):
        exported = [int(tid) for tid in (exported_raw or [])]
        pending = [int(tid) for tid in (pending_raw or [])]
        return exported, pending

    exported: list[int] = []
    pending: list[int] = []
    for div in payload.get("divisions") or []:
        if not isinstance(div, dict):
            continue
        for tid in div.get("exported") or []:
            exported.append(int(tid))
        for tid in div.get("pending") or []:
            pending.append(int(tid))
    return exported, pending


def _sim_cycle_embed(league_slug: str, payload: dict[str, Any], *, title: str) -> dict[str, Any]:
    phase = str(payload.get("phase") or "idle").strip().lower()
    logo = league_logo_emoji(league_slug)
    title_text = title[:256]
    if logo:
        title_text = f"{logo} {title_text}".strip()

    success_em = export_status_emoji(success=True, league_slug=league_slug) or "✅"
    fail_em = export_status_emoji(success=False, league_slug=league_slug) or "❌"

    exported_bits: list[str] = []
    pending_bits: list[str] = []
    exported_ids, pending_ids = _sim_cycle_team_fhm_ids(payload)
    for tid in exported_ids:
        if em := _team_emote_for_fhm_id(league_slug, int(tid)):
            exported_bits.append(em)
    for tid in pending_ids:
        if em := _team_emote_for_fhm_id(league_slug, int(tid)):
            pending_bits.append(em)

    lines: list[str] = []
    if exported_bits:
        lines.append(f"{success_em} {' '.join(exported_bits)}")
    if pending_bits:
        lines.append(f"{fail_em} {' '.join(pending_bits)}")

    description = "\n".join(lines)[:DISCORD_MAX_EMBED_DESC_LEN] if lines else None

    exported_count = int(payload.get("exported_count") or 0)
    total_teams = int(payload.get("total_teams") or 0)
    status_word = "In progress" if phase == "live" else "Closed"
    last_label = _sim_cycle_footer_timestamp(
        str(payload.get("last_updated_at") or "").strip(),
        phase=phase,
    )
    footer_text = f"{status_word} — {exported_count}/{total_teams} exported"
    if last_label:
        footer_text += f" · last update {last_label}"

    color = payload.get("embed_color")
    try:
        embed_color = int(color) if color is not None else sim_cycle_embed_color(league_slug)
    except (TypeError, ValueError):
        embed_color = sim_cycle_embed_color(league_slug)

    embed: dict[str, Any] = {
        "title": title_text,
        "description": description,
        "color": embed_color,
        "footer": {"text": footer_text[:2048]},
    }
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

    if event_key == "playoff_predictions":
        return _format_playoff_predictions_messages(
            league_slug,
            payload,
            title=title,
            max_parts=max(1, int(max_parts)),
        )

    if event_key == "playoff_bracket_update":
        return _format_playoff_bracket_messages(
            league_slug,
            payload,
            title=title,
            max_parts=max(1, int(max_parts)),
        )

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
            return _split_message_bodies(
                {"content": f"**{title}**", "embeds": [embed]},
                max_parts=max(1, int(max_parts)),
            )
        return [{"content": f"**{title}**"}]

    if event_key == "sim_cycle_update":
        embed = _sim_cycle_embed(league_slug, payload, title=title)
        if embed.get("description") or embed.get("title"):
            return _split_message_bodies({"embeds": [embed]}, max_parts=max(1, int(max_parts)))
        return [{"content": f"**{title}**"}]

    if event_key in ("trade_market_selling_posted", "trade_market_buying_posted"):
        body_full = _body_text(payload, full=True) or body_short
        embed = _trade_market_embed(payload, title=title, body=body_full, url=url)
        if embed.get("description") or embed.get("url"):
            msg = {"embeds": [embed]}
            mention = _team_gm_mention_line(payload)
            if mention:
                msg["content"] = mention
            return _split_message_bodies(msg, max_parts=max(1, int(max_parts)))
        return [{"content": f"**{title}**"}]

    if event_key in ("news_published", "gm_news_published", "admin_news_published") and _payload_has_image(payload):
        body_full = _body_text(payload, full=True) or body_short
        embed = _news_embed(league_slug, payload, title=title, body=body_full, url=url)
        if embed.get("description") or embed.get("url"):
            mention = _team_gm_mention_line(payload)
            msg = {"embeds": [embed]}
            if mention:
                msg["content"] = mention
            return _split_message_bodies(msg, max_parts=max(1, int(max_parts)))
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
        if team_line or payload.get("league_wide"):
            _append_team_gm_mention(lines, payload)
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
        _append_team_gm_mention(lines, payload)
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
        _append_team_gm_mention(lines, payload)
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
            _append_team_gm_mention(lines, payload)
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
            _append_team_gm_mention(lines, payload)
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
            _append_team_gm_mention(lines, payload)
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
