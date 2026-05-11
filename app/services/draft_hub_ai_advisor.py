"""Entertainment-only draft advice for the team on the clock (OpenAI or heuristic fallback)."""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.draft_hub_eligibility import eligible_players_ordered
from app.services.draft_hub_state import draft_eligibility_params, picked_player_ids
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.site_models import LeagueDraft

_CACHE: dict[tuple[int, int, int], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 50.0


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _player_line(session: Session, pl: Player, board_rank: int | None = None) -> str:
    rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None))
    ovr = compute_player_overall_100(
        pl.overall_ability,
        pl.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(pl),
    )
    pos = player_positions_display_label(pl) or (pl.position or "—")
    abi = pl.overall_ability
    pot = pl.overall_potential
    parts = [
        pl.full_name or f"Player #{pl.id}",
        pos,
    ]
    if board_rank is not None:
        parts.append(f"board #{board_rank}")
    if abi is not None:
        parts.append(f"ABI {round(float(abi), 1)}")
    if pot is not None:
        parts.append(f"POT {round(float(pot), 1)}")
    if ovr is not None:
        parts.append(f"OVR~{round(float(ovr))}")
    return " · ".join(parts)


def _sort_roster(session: Session, players: list[Player]) -> list[Player]:
    def ovr_key(pl: Player) -> float:
        rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None))
        o = compute_player_overall_100(
            pl.overall_ability,
            pl.overall_potential,
            rr,
            is_goalie=player_is_goalie_for_overall(pl),
        )
        return float(o) if o is not None else float("-inf")

    return sorted(players, key=lambda p: (-ovr_key(p), -(float(p.overall_potential or -1)), p.full_name or ""))


def _position_bucket(pl: Player) -> str:
    label = (player_positions_display_label(pl) or pl.position or "?").upper()
    if "G" in label:
        return "G"
    if "D" in label:
        return "D"
    return "F"


def _roster_shape(roster: list[Player]) -> tuple[str, list[str], set[str]]:
    exact_counts: dict[str, int] = {}
    bucket_counts = {"F": 0, "D": 0, "G": 0}
    for pl in roster:
        label = player_positions_display_label(pl) or pl.position or "?"
        exact_counts[label] = exact_counts.get(label, 0) + 1
        bucket = _position_bucket(pl)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    bucket_txt = ", ".join(f"{k}: {v}" for k, v in bucket_counts.items())
    exact_txt = ", ".join(f"{k}: {v}" for k, v in sorted(exact_counts.items(), key=lambda kv: (kv[1], kv[0]))[:8])
    summary = f"Broad roster count — {bucket_txt}."
    if exact_txt:
        summary += f" Thin exact-position hints — {exact_txt}."

    thin_exact = [k for k, _ in sorted(exact_counts.items(), key=lambda kv: (kv[1], kv[0]))[:3]]
    fewest_bucket = min(bucket_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    need_buckets = {fewest_bucket}
    if bucket_counts.get("G", 0) <= 2:
        need_buckets.add("G")
    return summary, thin_exact, need_buckets


def _need_fit_candidates(
    eligible_top: list[Player],
    rank_by_id: dict[int, int],
    need_buckets: set[str],
) -> list[Player]:
    def score(pl: Player) -> tuple[int, int, str]:
        rk = int(rank_by_id.get(pl.id, 999))
        need_bonus = 0 if _position_bucket(pl) in need_buckets else 18
        return (rk + need_bonus, rk, pl.full_name or "")

    pool = eligible_top[:24]
    ranked = sorted(pool, key=score)
    out: list[Player] = []
    seen: set[int] = set()
    for pl in ranked:
        if pl.id in seen:
            continue
        seen.add(pl.id)
        out.append(pl)
        if len(out) >= 6:
            break
    return out


def _error_payload(message: str) -> dict[str, Any]:
    """Surface a clear error to the API panel when the live model cannot answer."""
    return {
        "headline": None,
        "summary": None,
        "recommendations": [],
        "error": message,
    }


def build_draft_advice_prompt(
    session: Session,
    *,
    team: Team | None,
    team_name: str,
    round_no: int,
    overall: int,
    roster: list[Player],
    eligible_top: list[Player],
    rank_by_id: dict[int, int],
) -> str:
    lines = [
        f"Team on the clock: {team_name} (database id {team.id if team else 'unknown'})",
        f"Draft slot: Round {round_no}, overall #{overall}",
        "",
        "Current NHL roster on file (sorted roughly by strength; entertainment context only):",
    ]
    roster_s = _sort_roster(session, roster)
    shape_txt, thin_exact, need_buckets = _roster_shape(roster_s)
    cap = 28
    if not roster_s:
        lines.append("  • (no players with current_team_id set — treat as unknown depth)")
    else:
        for pl in roster_s[:cap]:
            lines.append(f"  • {_player_line(session, pl)}")
        if len(roster_s) > cap:
            lines.append(f"  • … plus {len(roster_s) - cap} more")
    lines.extend(
        [
            "",
            "Roster need snapshot:",
            f"  • {shape_txt}",
            f"  • Possible thinner spots: {', '.join(thin_exact) if thin_exact else 'unknown'}",
            "",
            "Top available prospects from the league eligibility list (already excludes picked players):",
            "",
        ]
    )
    for pl in eligible_top[:14]:
        rk = rank_by_id.get(pl.id)
        lines.append(f"  • {_player_line(session, pl, board_rank=rk)}")
    need_fit = _need_fit_candidates(eligible_top, rank_by_id, need_buckets)
    lines.extend(
        [
            "",
            "Need-fit candidates to consider (these may be below pure BPA, but should still be respectable board values):",
            "",
        ]
    )
    for pl in need_fit[:6]:
        rk = rank_by_id.get(pl.id)
        lines.append(f"  • {_player_line(session, pl, board_rank=rk)}")
    return "\n".join(lines)


def fetch_draft_hub_ai_advice(
    session: Session,
    league_slug: str,
    draft: LeagueDraft,
    *,
    team_id: int,
    team_name: str,
    round_no: int,
    overall: int,
) -> dict[str, Any]:
    """Return headline, summary, recommendations[{player_id, player_name, blurb}], fallback."""
    cache_key = (int(draft.id), int(overall), int(team_id))
    now = time.time()
    hit = _CACHE.get(cache_key)
    if hit and hit[0] > now:
        return dict(hit[1])

    params = draft_eligibility_params(draft)
    picked = picked_player_ids(session, draft.id)
    eligible_all = [p for p in eligible_players_ordered(session, league_slug, params) if p.id not in picked]
    rank_by_id = {p.id: i + 1 for i, p in enumerate(eligible_all)}
    eligible_top = eligible_all[:14]

    roster = list(
        session.scalars(
            select(Player).where(Player.current_team_id == int(team_id), Player.retired.is_(False))
        ).unique().all()
    )
    tm = session.get(Team, int(team_id))

    if not eligible_top:
        out = _error_payload(
            "The board looks empty from this site's filters — nothing for the bot to rank."
        )
        _CACHE[cache_key] = (now + _CACHE_TTL_SEC, out)
        return out

    api_key = str(current_app.config.get("TRADE_AI_OPENAI_API_KEY") or "").strip()
    model = str(current_app.config.get("TRADE_AI_OPENAI_MODEL") or "gpt-4o-mini").strip()

    allowed_ids = {p.id for p in eligible_all[:40]}

    if not api_key:
        current_app.logger.warning("Draft hub AI: no OPENAI_API_KEY configured")
        return _error_payload(
            "AI desk is unavailable — server has no OpenAI API key configured."
        )

    block = build_draft_advice_prompt(
        session,
        team=tm,
        team_name=team_name,
        round_no=round_no,
        overall=overall,
        roster=roster,
        eligible_top=eligible_top,
        rank_by_id=rank_by_id,
    )
    system = (
        "You are a sharp, good-natured fantasy hockey armchair GM bot. "
        "ENTERTAINMENT ONLY: never imply official league approval or real contract knowledge. "
        "Recommend 1–3 draft picks for the team on the clock using roster needs, positional scarcity, and best player available. "
        "Do NOT automatically choose the top player available; a lower-ranked player is fine when the roster-fit case is better. "
        "Still respect board value: avoid extreme reaches unless the user-provided data clearly supports the fit. "
        "In each blurb, name the reason: need-fit, upside, positional scarcity, or safer BPA. "
        "Be concise and playful—no slurs, no harassment. "
        "Output STRICT JSON with keys: "
        "headline (short, under 90 chars), "
        "summary (2–4 sentences, plain text), "
        "recommendations (array of 1–3 objects with keys player_id (int), player_name (string), blurb (one sentence))."
        " Every player_id MUST be copied exactly from the prospect list in the user message."
    )
    user_msg = (
        "Who should this team take next? Use ONLY player_ids that appear in the prospect list.\n\n" + block
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.75,
        "max_tokens": 650,
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

    def _finish(data: dict[str, Any]) -> dict[str, Any]:
        _CACHE[cache_key] = (now + _CACHE_TTL_SEC, data)
        return data

    try:
        with urlopen(req, timeout=55) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        current_app.logger.warning("Draft hub AI HTTPError: %s %s", e.code, err_body)
        return _finish(
            _error_payload(
                f"AI desk request rejected (HTTP {e.code}). Check API key, model name, and OpenAI billing."
            )
        )
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        current_app.logger.warning("Draft hub AI request failed: %s", e)
        return _finish(
            _error_payload("AI desk could not reach the model right now. Try again in a moment.")
        )

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return _finish(_error_payload("AI desk got an unreadable response from the model."))

    try:
        parsed = json.loads(_strip_json_fence(str(content)))
    except json.JSONDecodeError:
        return _finish(_error_payload("AI desk could not parse the model's JSON reply."))

    headline = str(parsed.get("headline") or "Draft desk").strip()[:200]
    summary = str(parsed.get("summary") or "").strip()
    raw_recs = parsed.get("recommendations")
    recommendations: list[dict[str, Any]] = []
    if isinstance(raw_recs, list):
        for item in raw_recs[:4]:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("player_id"))
            except (TypeError, ValueError):
                continue
            if pid not in allowed_ids:
                continue
            pl = session.get(Player, pid)
            pname = str(item.get("player_name") or (pl.full_name if pl else "")).strip()
            blurb = str(item.get("blurb") or "").strip()
            if pl and pname != (pl.full_name or ""):
                pname = pl.full_name or pname
            if pname and blurb:
                recommendations.append({"player_id": pid, "player_name": pname, "blurb": blurb})

    if not summary:
        summary = "The model wandered offside — treating this as a soft suggestion only."

    if not recommendations:
        return _finish(
            _error_payload("AI desk returned no valid recommendations from the eligible board.")
        )

    return _finish(
        {
            "headline": headline,
            "summary": summary,
            "recommendations": recommendations,
        }
    )
