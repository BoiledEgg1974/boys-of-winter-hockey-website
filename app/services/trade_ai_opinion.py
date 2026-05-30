"""Entertainment-only AI opinions for hypothetical GM trades (OpenAI Chat Completions)."""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row
from app.services.trade_log import TradeLogRow, format_recent_trades_for_prompt, trade_log_source_label
from app.services.trade_tool import describe_drag_key, format_ledger_summary

_LAST_CALL_BY_USER: dict[int, float] = {}
_MIN_INTERVAL_SEC = 8.0


def _error_payload(message: str, details: str | None = None) -> dict[str, Any]:
    """Caller (route) should translate the ``error`` key into an HTTP 5xx so the UI alerts cleanly."""
    return {"error": message, "details": details or ""}


def _extract_openai_error_message(body: str) -> str | None:
    """Best-effort extraction of the human-readable message from an OpenAI error body."""
    if not body:
        return None
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()[:280] or None
    err = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:280]
    return None


def _uses_completion_token_limit(model: str) -> bool:
    """Newer OpenAI reasoning/GPT-5 models reject the legacy max_tokens field."""
    m = (model or "").strip().lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def build_trade_prompt_block(
    session: Session,
    from_team: Team | None,
    to_team: Team | None,
    left: list[str],
    right: list[str],
    notes: str,
    *,
    league_slug: str = "",
    recent_trades_context: str = "",
) -> str:
    base = format_ledger_summary(
        session, from_team, to_team, left, right, league_slug=league_slug
    )
    extras: list[str] = ["", "Extra roster context (OVR ~100 scale where available):"]
    for label, keys in (("Outgoing package (left → right)", left), ("Return package (right → left)", right)):
        extras.append(f"  {label}:")
        for k in keys:
            line = f"    • {describe_drag_key(session, k)}"
            if k.startswith("player:"):
                try:
                    pid = int(k.split(":", 1)[1])
                except (ValueError, IndexError):
                    extras.append(line)
                    continue
                pl = session.get(Player, pid)
                if pl:
                    rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None))
                    ovr = compute_player_overall_100(
                        pl.overall_ability,
                        pl.overall_potential,
                        rr,
                        is_goalie=player_is_goalie_for_overall(pl),
                    )
                    abi = pl.overall_ability
                    pot = pl.overall_potential
                    line += f" | ABI {abi} POT {pot}" if abi is not None else line
                    if ovr is not None:
                        line += f" | OVR~{round(float(ovr))}"
            extras.append(line)
        if not keys:
            extras.append("    • (none)")
    notes = (notes or "").strip()
    if notes:
        extras.extend(["", "GM notes (flavor only):", notes[:2000]])
    out = base + "\n".join(extras)
    ctx = (recent_trades_context or "").strip()
    if ctx:
        out += "\n\n" + ctx
    return out


def build_logged_trade_prompt_block(row: TradeLogRow) -> str:
    """Prompt body for an existing trade-log row (not a hypothetical ledger)."""
    when = ""
    if row.trade_date:
        when = row.trade_date.isoformat()
    elif row.sort_at and row.sort_at.year > 1970:
        when = row.sort_at.strftime("%Y-%m-%d")
    ta = row.team_a_label or (row.team_a.full_display_name() if row.team_a else "?")
    tb = row.team_b_label or (row.team_b.full_display_name() if row.team_b else "?")
    src = trade_log_source_label(row.source)
    lines = [
        f"Logged trade ({src})",
        f"Date: {when or 'unknown'}",
        f"Teams: {ta} ↔ {tb}",
        f"Headline: {row.title}",
    ]
    body = (row.body or "").strip()
    direction = _logged_trade_direction_block(body)
    if direction:
        lines.extend(["", direction])
    if body:
        lines.extend(["", "Summary / details:", body[:4000]])
    return "\n".join(lines)


def _logged_trade_direction_block(body: str) -> str:
    """Turn manual ``Team sends:`` summaries into unambiguous traded-away/received lines."""
    blocks = [b.strip() for b in (body or "").strip().split("\n\n", 1)]
    if len(blocks) != 2:
        return ""

    def _parse_sends_block(block: str) -> tuple[str, list[str]] | None:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        heading = lines[0]
        if not heading.lower().endswith(" sends:"):
            return None
        team = heading[: -len(" sends:")].strip()
        assets = lines[1:]
        if not team or not assets:
            return None
        return team, assets

    left = _parse_sends_block(blocks[0])
    right = _parse_sends_block(blocks[1])
    if not left or not right:
        return ""
    team_a, sent_by_a = left
    team_b, sent_by_b = right
    fmt_a = "; ".join(sent_by_a)
    fmt_b = "; ".join(sent_by_b)
    return "\n".join(
        [
            "Directional interpretation (authoritative):",
            f"- {team_a} traded away: {fmt_a}",
            f"- {team_b} traded away: {fmt_b}",
            f"- {team_a} received from {team_b}: {fmt_b}",
            f"- {team_b} received from {team_a}: {fmt_a}",
            "Do not describe an asset as acquired by the same team whose 'sends' block lists it.",
        ]
    )


def recent_trades_prompt_block(rows: list[TradeLogRow], *, limit: int = 12) -> str:
    return format_recent_trades_for_prompt(rows, limit=limit)


def _openai_trade_json_response(
    *,
    user_id: int,
    system: str,
    user_msg: str,
) -> dict[str, Any]:
    """Shared OpenAI JSON call with rate limiting; returns parsed opinion or error dict."""
    now = time.time()
    last = _LAST_CALL_BY_USER.get(user_id, 0.0)
    if now - last < _MIN_INTERVAL_SEC:
        wait = int(_MIN_INTERVAL_SEC - (now - last)) + 1
        return {
            "verdict": "Slow down, hotshot",
            "opinion": f"Give the bot {wait}s to catch its breath before another take.",
            "suggestions": [],
            "rate_limited": True,
        }
    _LAST_CALL_BY_USER[user_id] = now

    api_key = str(current_app.config.get("TRADE_AI_OPENAI_API_KEY") or "").strip()
    model = str(current_app.config.get("TRADE_AI_OPENAI_MODEL") or "gpt-4o-mini").strip()

    if not api_key:
        current_app.logger.warning("Trade AI: no OPENAI_API_KEY configured (model=%s)", model)
        return _error_payload(
            "AI Trade Tool is unavailable — server has no OpenAI API key configured.",
            details=f"Model in use: {model}. Set OPENAI_API_KEY in .env and restart the app.",
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
    }
    if _uses_completion_token_limit(model):
        payload["max_completion_tokens"] = 600
    else:
        payload["temperature"] = 0.85
        payload["max_tokens"] = 600
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        current_app.logger.warning("Trade AI HTTPError: %s %s (model=%s)", e.code, err_body, model)
        api_msg = _extract_openai_error_message(err_body)
        detail_bits: list[str] = []
        if api_msg:
            detail_bits.append(api_msg)
        detail_bits.append(f"Model in use: {model}")
        return _error_payload(
            f"AI Trade Tool request rejected (HTTP {e.code}). Check API key, model name, and OpenAI billing.",
            details=" — ".join(detail_bits),
        )
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        current_app.logger.warning("Trade AI request failed: %s", e)
        return _error_payload("AI Trade Tool could not reach the model right now. Try again in a moment.")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return _error_payload("AI Trade Tool got an unreadable response from the model.")

    try:
        parsed = json.loads(_strip_json_fence(str(content)))
    except json.JSONDecodeError:
        return _error_payload("AI Trade Tool could not parse the model's JSON reply.")

    verdict = str(parsed.get("verdict") or "No verdict").strip()[:200]
    opinion = str(parsed.get("opinion") or "").strip()
    sug = parsed.get("suggestions")
    suggestions: list[str] = []
    if isinstance(sug, list):
        for x in sug[:6]:
            if isinstance(x, str) and x.strip():
                suggestions.append(x.strip())
    elif isinstance(sug, str) and sug.strip():
        suggestions.append(sug.strip())
    if not opinion:
        opinion = "Even the bot is speechless—try tweaking the packages and ask again."

    return {
        "verdict": verdict,
        "opinion": opinion,
        "suggestions": suggestions,
    }


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def fetch_trade_ai_opinion(
    session: Session,
    *,
    user_id: int,
    from_team: Team | None,
    to_team: Team | None,
    left: list[str],
    right: list[str],
    notes: str,
    league_slug: str = "",
    recent_trades_context: str = "",
) -> dict[str, Any]:
    """Return dict: verdict, opinion, suggestions (list[str]), fallback (bool)."""
    block = build_trade_prompt_block(
        session,
        from_team,
        to_team,
        left,
        right,
        notes,
        league_slug=league_slug,
        recent_trades_context=recent_trades_context,
    )

    system = (
        "You are a witty, knowledgeable hockey armchair GM bot on a fantasy/sim league website. "
        "Your job is ENTERTAINMENT ONLY: never claim official league approval, salary cap legality, "
        "or real-world trade acceptance. Keep it clever and fun—short metaphors, light chirps, no slurs, "
        "no harassment. Output STRICT JSON with keys: "
        'verdict (short punchy headline, under 80 chars), '
        'opinion (2-4 sentences, plain text, no HTML), '
        'suggestions (array of 2-4 short strings: concrete ideas to balance the deal, still entertainment).'
    )
    user_msg = (
        "Here is a hypothetical trade scenario. Give your spicy-but-good-natured read and how to even it up. "
        "If recent league trades are listed, you may reference patterns—but judge only this scenario.\n\n"
        f"{block}"
    )
    return _openai_trade_json_response(user_id=user_id, system=system, user_msg=user_msg)


def fetch_logged_trade_ai_opinion(
    *,
    user_id: int,
    row: TradeLogRow,
) -> dict[str, Any]:
    """AI take on a completed trade already in the league trade log."""
    block = build_logged_trade_prompt_block(row)
    system = (
        "You are a witty hockey armchair GM bot on a fantasy/sim league website. "
        "This trade ALREADY HAPPENED in the league log—your job is ENTERTAINMENT ONLY: "
        "react to whether it looks lopsided, fun, or sneaky-good in hindsight. "
        "Never claim official league approval or retroactive veto power. "
        "Output STRICT JSON with keys: "
        'verdict (short punchy headline, under 80 chars), '
        'opinion (2-4 sentences, plain text, no HTML), '
        'suggestions (array of 1-3 short strings: what each side might chase next, still entertainment).'
    )
    user_msg = (
        "Here is a completed trade from the league trade log. Give your spicy-but-good-natured hindsight take.\n\n"
        f"{block}"
    )
    return _openai_trade_json_response(user_id=user_id, system=system, user_msg=user_msg)
