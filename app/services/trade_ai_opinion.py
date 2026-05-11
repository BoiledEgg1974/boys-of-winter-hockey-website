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
from app.services.trade_tool import describe_drag_key, format_ledger_summary

_LAST_CALL_BY_USER: dict[int, float] = {}
_MIN_INTERVAL_SEC = 8.0


def _error_payload(message: str) -> dict[str, Any]:
    """Caller (route) should translate the ``error`` key into an HTTP 5xx so the UI alerts cleanly."""
    return {"error": message}


def build_trade_prompt_block(
    session: Session, from_team: Team | None, to_team: Team | None, left: list[str], right: list[str], notes: str
) -> str:
    base = format_ledger_summary(session, from_team, to_team, left, right)
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
    return base + "\n".join(extras)


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
) -> dict[str, Any]:
    """Return dict: verdict, opinion, suggestions (list[str]), fallback (bool)."""
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
        current_app.logger.warning("Trade AI: no OPENAI_API_KEY configured")
        return _error_payload("AI Trade Tool is unavailable — server has no OpenAI API key configured.")

    block = build_trade_prompt_block(session, from_team, to_team, left, right, notes)

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
        "Here is a hypothetical trade scenario. Give your spicy-but-good-natured read and how to even it up.\n\n"
        f"{block}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.85,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }
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
        current_app.logger.warning("Trade AI HTTPError: %s %s", e.code, err_body)
        return _error_payload(
            f"AI Trade Tool request rejected (HTTP {e.code}). Check API key, model name, and OpenAI billing."
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
