"""Entertainment-only draft advice for the team on the clock (OpenAI with local heuristic fallback)."""
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
# Cached entries last until the (draft, overall_pick, team_id) tuple changes — i.e. the
# next team is on the clock. Refreshing the page costs zero tokens. Errors get a much
# shorter TTL so a transient failure does not lock the panel for the whole pick window.
_CACHE_TTL_SEC = 24 * 3600.0
_ERROR_CACHE_TTL_SEC = 30.0


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
        f"[id={pl.id}]",
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


def _error_payload(message: str, details: str | None = None) -> dict[str, Any]:
    """Surface a clear error to the API panel when the live model cannot answer."""
    return {
        "headline": None,
        "summary": None,
        "recommendations": [],
        "error": message,
        "details": details,
    }


def _uses_completion_token_limit(model: str) -> bool:
    """Newer OpenAI reasoning/GPT-5 models reject the legacy max_tokens field."""
    m = (model or "").strip().lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


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


def _heuristic_draft_advice(
    session: Session,
    *,
    team_name: str,
    round_no: int,
    overall: int,
    roster: list[Player],
    eligible_all: list[Player],
    eligible_top: list[Player],
    rank_by_id: dict[int, int],
) -> dict[str, Any]:
    """Rules-based suggestions only (no external API). Same keys as a successful model payload."""
    roster_s = _sort_roster(session, roster)
    _shape_txt, thin_exact, need_buckets = _roster_shape(roster_s)
    need_fit = _need_fit_candidates(eligible_top, rank_by_id, need_buckets)

    def _one_rec(pl: Player, blurb: str) -> dict[str, Any]:
        return {
            "player_id": int(pl.id),
            "player_name": (pl.full_name or f"Player #{pl.id}").strip(),
            "blurb": blurb,
        }

    recommendations: list[dict[str, Any]] = []
    seen: set[int] = set()

    bpa = eligible_all[0] if eligible_all else None
    if bpa is not None:
        rk = int(rank_by_id.get(bpa.id, 1))
        recommendations.append(
            _one_rec(
                bpa,
                f"Best player available on the site's eligibility board right now (published rank #{rk}).",
            )
        )
        seen.add(int(bpa.id))

    need_label = ", ".join(sorted(need_buckets)) if need_buckets else "balance"
    for pl in need_fit:
        if pl.id in seen:
            continue
        rk = int(rank_by_id.get(pl.id, 0))
        recommendations.append(
            _one_rec(
                pl,
                f"Need-weighted lean (board #{rk}) favoring thinner {need_label} depth vs pure BPA.",
            )
        )
        seen.add(int(pl.id))
        if len(recommendations) >= 3:
            break

    if len(recommendations) < 2 and len(eligible_all) > 1:
        alt = eligible_all[1]
        if alt.id not in seen:
            rk2 = int(rank_by_id.get(alt.id, 0))
            recommendations.append(
                _one_rec(
                    alt,
                    f"Next BPA signal (board #{rk2}) if you want the next-highest rated profile.",
                )
            )
            seen.add(int(alt.id))

    if not recommendations and eligible_all:
        pl0 = eligible_all[0]
        recommendations.append(
            _one_rec(pl0, "Top available name on the eligibility list.")
        )

    thin_note = (
        f" Depth looks thinner at: {', '.join(thin_exact)}."
        if thin_exact
        else " Positional counts look fairly even — lean on board rank or upside."
    )
    summary = (
        f"Heuristic desk (no live language model): quick rules for {team_name} in round {round_no}, "
        f"overall #{overall}.{thin_note} "
        "Use the BPA line as your anchor, then compare the need-weighted option to your own read."
    )
    headline = f"Heuristic desk · R{round_no} pick {overall}"
    headline = headline.strip()[:90]

    return {
        "headline": headline,
        "summary": summary.strip()[:1200],
        "recommendations": recommendations[:3],
    }


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
        _CACHE[cache_key] = (now + _ERROR_CACHE_TTL_SEC, out)
        return out

    api_key = str(current_app.config.get("TRADE_AI_OPENAI_API_KEY") or "").strip()
    model = str(current_app.config.get("TRADE_AI_OPENAI_MODEL") or "gpt-4o-mini").strip()

    # Validate against the full eligible pool, not just top 40, so a model that loosely
    # references a name we did not list explicitly can still be matched after a name lookup.
    allowed_by_id: dict[int, Player] = {p.id: p for p in eligible_all}
    allowed_ids = set(allowed_by_id.keys())
    name_to_id: dict[str, int] = {}
    for pid, pl in allowed_by_id.items():
        nm = (pl.full_name or "").strip().lower()
        if nm:
            name_to_id.setdefault(nm, pid)

    def _cache_and_return(data: dict[str, Any]) -> dict[str, Any]:
        ttl = _ERROR_CACHE_TTL_SEC if data.get("error") else _CACHE_TTL_SEC
        _CACHE[cache_key] = (now + ttl, data)
        return data

    def _heuristic_payload() -> dict[str, Any]:
        return _heuristic_draft_advice(
            session,
            team_name=team_name,
            round_no=round_no,
            overall=overall,
            roster=roster,
            eligible_all=eligible_all,
            eligible_top=eligible_top,
            rank_by_id=rank_by_id,
        )

    if current_app.config.get("DRAFT_HUB_AI_HEURISTIC_ONLY"):
        current_app.logger.info(
            "Draft hub AI: using heuristic desk (DRAFT_HUB_AI_HEURISTIC_ONLY is set). Skipping OpenAI."
        )
        return _cache_and_return(_heuristic_payload())

    if not api_key:
        current_app.logger.info("Draft hub AI: using heuristic desk (no TRADE_AI_OPENAI_API_KEY). model=%s", model)
        return _cache_and_return(_heuristic_payload())

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
        "recommendations (array of 1–3 objects with keys player_id (int), player_name (string), blurb (one sentence)). "
        "Each prospect in the user message is tagged with [id=N]; the player_id field MUST be exactly that integer N. "
        "Never invent player_ids and never reuse IDs from your own training data."
    )
    user_msg = (
        "Who should this team take next? Only pick from prospects in the user-supplied list, "
        "and copy the integer that appears in their [id=N] tag into the player_id field.\n\n"
        + block
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
        payload["max_completion_tokens"] = 650
    else:
        payload["temperature"] = 0.75
        payload["max_tokens"] = 650
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
        with urlopen(req, timeout=55) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        current_app.logger.warning(
            "Draft hub AI HTTPError (using heuristic fallback): %s %s (model=%s)", e.code, err_body, model
        )
        return _cache_and_return(_heuristic_payload())
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        current_app.logger.warning("Draft hub AI request failed (using heuristic fallback): %s", e)
        return _cache_and_return(_heuristic_payload())

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        current_app.logger.warning("Draft hub AI unreadable choices payload (using heuristic fallback).")
        return _cache_and_return(_heuristic_payload())

    try:
        parsed = json.loads(_strip_json_fence(str(content)))
    except json.JSONDecodeError:
        current_app.logger.warning("Draft hub AI JSON parse failed (using heuristic fallback).")
        return _cache_and_return(_heuristic_payload())

    headline = str(parsed.get("headline") or "Draft desk").strip()[:200]
    summary = str(parsed.get("summary") or "").strip()
    raw_recs = parsed.get("recommendations")
    recommendations: list[dict[str, Any]] = []
    seen_pids: set[int] = set()
    if isinstance(raw_recs, list):
        for item in raw_recs[:4]:
            if not isinstance(item, dict):
                continue
            pid: int | None = None
            raw_pid = item.get("player_id")
            try:
                if raw_pid is not None and str(raw_pid).strip() != "":
                    pid = int(raw_pid)
            except (TypeError, ValueError):
                pid = None
            nm_in = str(item.get("player_name") or "").strip()
            if pid is None or pid not in allowed_ids:
                # Last-resort: try to match by the name the model returned.
                key = nm_in.lower()
                if key and key in name_to_id:
                    pid = name_to_id[key]
                else:
                    continue
            if pid in seen_pids:
                continue
            pl = allowed_by_id.get(pid) or session.get(Player, pid)
            pname = nm_in or (pl.full_name if pl else "")
            blurb = str(item.get("blurb") or "").strip()
            if pl and pname != (pl.full_name or ""):
                pname = pl.full_name or pname
            if pname and blurb:
                recommendations.append({"player_id": pid, "player_name": pname, "blurb": blurb})
                seen_pids.add(pid)

    if not summary:
        summary = "The model wandered offside — treating this as a soft suggestion only."

    if not recommendations:
        current_app.logger.warning("Draft hub AI returned no valid recs (using heuristic fallback).")
        return _cache_and_return(_heuristic_payload())

    return _cache_and_return(
        {
            "headline": headline,
            "summary": summary,
            "recommendations": recommendations,
        }
    )
