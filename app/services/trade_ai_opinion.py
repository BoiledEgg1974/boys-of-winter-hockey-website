"""Entertainment-only AI opinions for hypothetical GM trades (OpenAI Chat Completions)."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row
from app.services.trade_log import TradeLogRow, format_recent_trades_for_prompt, trade_log_card_view, trade_log_source_label
from app.services.trade_tool import describe_drag_key, format_ledger_summary

_LAST_CALL_BY_USER: dict[int, float] = {}
_MIN_INTERVAL_SEC = 8.0


def _error_payload(message: str, details: str | None = None) -> dict[str, Any]:
    """Caller (route) should translate the ``error`` key into an HTTP 5xx so the UI alerts cleanly."""
    return {"error": message, "details": details or ""}


def _redact_provider_secrets(text: str) -> str:
    """Avoid echoing API key material into member alerts or server logs."""
    return re.sub(r"sk-[A-Za-z0-9_*.-]+", "sk-...redacted", str(text or ""))


def _fallback_payload(verdict: str, opinion: str, suggestions: list[str]) -> dict[str, Any]:
    """Local entertainment take used when the model provider is unavailable."""
    return {
        "verdict": verdict,
        "opinion": opinion,
        "suggestions": suggestions,
        "fallback": True,
    }


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
            return _redact_provider_secrets(msg.strip())[:280]
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
    out += "\n\n" + _hypothetical_trade_direction_block(session, from_team, to_team, left, right)
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
    direction = _logged_trade_direction_block(row)
    if direction:
        lines.extend(["", direction])
    if body and not direction:
        lines.extend(["", "Summary / details:", body[:4000]])
    return "\n".join(lines)


def _logged_trade_direction_block(row: TradeLogRow) -> str:
    """Use the same acquired-side interpretation as the visible trade-log card."""
    view = trade_log_card_view(row)
    team_a = view.team_a.team_label
    team_b = view.team_b.team_label
    acquired_by_a = list(view.team_a.acquired)
    acquired_by_b = list(view.team_b.acquired)
    if not acquired_by_a and not acquired_by_b:
        return ""
    fmt_a = "; ".join(acquired_by_a) or "future considerations"
    fmt_b = "; ".join(acquired_by_b) or "future considerations"
    return "\n".join(
        [
            "Visible trade card interpretation (authoritative):",
            f"- {team_a} acquired: {fmt_a}",
            f"- {team_b} acquired: {fmt_b}",
            f"- Do not reverse these sides. If the public card shows an asset under {team_a}, treat it as acquired by {team_a}.",
        ]
    )


def recent_trades_prompt_block(rows: list[TradeLogRow], *, limit: int = 12) -> str:
    return format_recent_trades_for_prompt(rows, limit=limit)


def _team_label(team: Team | None, fallback: str) -> str:
    if team is None:
        return fallback
    try:
        return team.full_display_name()
    except Exception:
        return fallback


def _norm_text(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    asciiish = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", asciiish.lower()).strip()


def _asset_label(session: Session, key: str) -> str:
    try:
        return describe_drag_key(session, key)
    except Exception:
        return str(key)


def _asset_labels(session: Session, keys: list[str]) -> list[str]:
    return [_asset_label(session, k) for k in keys]


def _hypothetical_trade_direction_block(
    session: Session,
    from_team: Team | None,
    to_team: Team | None,
    left: list[str],
    right: list[str],
) -> str:
    from_name = _team_label(from_team, "Team A")
    to_name = _team_label(to_team, "Team B")
    left_assets = "; ".join(_asset_labels(session, left)) or "(nothing)"
    right_assets = "; ".join(_asset_labels(session, right)) or "(nothing)"
    return "\n".join(
        [
            "Directional interpretation (authoritative):",
            f"- {from_name} traded away: {left_assets}",
            f"- {to_name} traded away: {right_assets}",
            f"- {from_name} received from {to_name}: {right_assets}",
            f"- {to_name} received from {from_name}: {left_assets}",
            "The verdict headline must match this direction. If you say one received the stronger package, do not headline that same team as fleeced, robbed, or getting the short end.",
        ]
    )


def _opinion_says_first_package_beats_second(
    opinion: str,
    first_labels: list[str],
    second_labels: list[str],
) -> bool:
    text = _norm_text(opinion)
    if not text:
        return False
    phrases = (
        "contribute more than",
        "more value than",
        "more upside than",
        "better package than",
        "stronger package than",
        "outweigh",
        "outweighs",
        "worth more than",
    )
    phrase_positions = [text.find(p) for p in phrases if text.find(p) >= 0]
    if not phrase_positions:
        return False
    pos = min(phrase_positions)
    before = text[:pos]
    after = text[pos:]

    def _mentions(labels: list[str], haystack: str) -> bool:
        for label in labels:
            normalized = _norm_text(label)
            # Player names may lose accents or be shortened by the model; matching any
            # distinctive name part is enough for a contradiction sanity check.
            parts = [p for p in re.split(r"[^a-z0-9]+", normalized) if len(p) >= 4]
            if normalized and normalized in haystack:
                return True
            if any(part in haystack for part in parts):
                return True
        return False

    return _mentions(first_labels, before) and _mentions(second_labels, after)


def _negative_verdict_target(verdict: str, team_names: dict[str, str]) -> str | None:
    text = _norm_text(verdict)
    if not text:
        return None
    negative_markers = (
        "short end",
        "fleec",
        "robbed",
        "hosed",
        "shaft",
        "lost this",
        "lose this",
        "takes the hit",
        "overpaid",
    )
    if not any(marker in text for marker in negative_markers):
        return None
    for key, name in team_names.items():
        normalized = _norm_text(name)
        parts = [p for p in re.split(r"[^a-z0-9]+", normalized) if len(p) >= 4]
        if normalized and normalized in text:
            return key
        if any(part in text for part in parts):
            return key
    return None


def _guard_hypothetical_trade_consistency(
    session: Session,
    payload: dict[str, Any],
    *,
    from_team: Team | None,
    to_team: Team | None,
    left: list[str],
    right: list[str],
) -> dict[str, Any]:
    if payload.get("error"):
        return payload
    verdict = str(payload.get("verdict") or "")
    opinion = str(payload.get("opinion") or "")
    from_name = _team_label(from_team, "Team A")
    to_name = _team_label(to_team, "Team B")
    target = _negative_verdict_target(verdict, {"from": from_name, "to": to_name})
    if not target:
        return payload

    left_labels = _asset_labels(session, left)
    right_labels = _asset_labels(session, right)
    # Left assets are received by the to-team; right assets are received by the from-team.
    if target == "to" and _opinion_says_first_package_beats_second(opinion, left_labels, right_labels):
        payload = dict(payload)
        payload["verdict"] = f"{from_name} get the short end of the stick"
        payload["consistency_guard"] = True
    elif target == "from" and _opinion_says_first_package_beats_second(opinion, right_labels, left_labels):
        payload = dict(payload)
        payload["verdict"] = f"{to_name} get the short end of the stick"
        payload["consistency_guard"] = True
    return payload


def _local_hypothetical_trade_opinion(
    session: Session,
    *,
    from_team: Team | None,
    to_team: Team | None,
    left: list[str],
    right: list[str],
    notes: str,
) -> dict[str, Any]:
    """Rule-based fallback so members still get a useful trade read if OpenAI rejects config."""
    left_count = len(left)
    right_count = len(right)
    from_name = _team_label(from_team, "Team A")
    to_name = _team_label(to_team, "Team B")
    left_preview = ", ".join(_asset_label(session, k) for k in left[:3]) or "nothing"
    right_preview = ", ".join(_asset_label(session, k) for k in right[:3]) or "nothing"
    if left_count > right_count + 1:
        verdict = f"{to_name} is loading up"
        lean = f"{from_name} is sending a bigger pile, so the return needs quality or cap/roster logic to make it sing."
    elif right_count > left_count + 1:
        verdict = f"{from_name} is asking for a haul"
        lean = f"{to_name} is sending more pieces, so {from_name} better be giving up the best asset in the deal."
    else:
        verdict = "Close enough for the war room"
        lean = "The package sizes are close, so this comes down to who is getting the best player or most useful pick."
    note_line = " The GM note helps explain the angle." if (notes or "").strip() else ""
    opinion = (
        f"Local scout take: {from_name} sends {left_preview}; {to_name} sends {right_preview}. "
        f"{lean}{note_line} The live AI provider is unavailable right now, so treat this as a quick desk-check."
    )
    suggestions = [
        "If one side is getting the clear best player, add a mid-round pick or useful depth piece the other way.",
        "Check roster need first: a fair asset swap can still be a bad fit.",
        "Use recent league trades as the final sanity check before sending it to another GM.",
    ]
    return _fallback_payload(verdict, opinion, suggestions)


def _local_logged_trade_opinion(row: TradeLogRow) -> dict[str, Any]:
    ta = row.team_a_label or (row.team_a.full_display_name() if row.team_a else "Team A")
    tb = row.team_b_label or (row.team_b.full_display_name() if row.team_b else "Team B")
    title = (row.title or "Logged trade").strip()
    return _fallback_payload(
        "Archivist's quick take",
        (
            f"Local scout take on {title}: {ta} and {tb} made the move, and the fun part is judging "
            "whether the best asset or the better fit carried the day. The live AI provider is unavailable "
            "right now, so this is a quick fallback read rather than a full bot breakdown."
        ),
        [
            "Look for who received the highest-upside player.",
            "Check whether the pick value matches the player value.",
            "Revisit after a few sim weeks to see which side filled the bigger need.",
        ],
    )


def _openai_trade_json_response(
    *,
    user_id: int,
    system: str,
    user_msg: str,
    fallback: dict[str, Any] | None = None,
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
        if fallback is not None:
            return fallback
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
        current_app.logger.warning(
            "Trade AI HTTPError: %s %s (model=%s)",
            e.code,
            _redact_provider_secrets(err_body),
            model,
        )
        if fallback is not None:
            return fallback
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
        if fallback is not None:
            return fallback
        return _error_payload("AI Trade Tool could not reach the model right now. Try again in a moment.")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        if fallback is not None:
            return fallback
        return _error_payload("AI Trade Tool got an unreadable response from the model.")

    try:
        parsed = json.loads(_strip_json_fence(str(content)))
    except json.JSONDecodeError:
        if fallback is not None:
            return fallback
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
    fallback = _local_hypothetical_trade_opinion(
        session,
        from_team=from_team,
        to_team=to_team,
        left=left,
        right=right,
        notes=notes,
    )
    out = _openai_trade_json_response(
        user_id=user_id,
        system=system,
        user_msg=user_msg,
        fallback=fallback,
    )
    return _guard_hypothetical_trade_consistency(
        session,
        out,
        from_team=from_team,
        to_team=to_team,
        left=left,
        right=right,
    )


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
    return _openai_trade_json_response(
        user_id=user_id,
        system=system,
        user_msg=user_msg,
        fallback=_local_logged_trade_opinion(row),
    )
