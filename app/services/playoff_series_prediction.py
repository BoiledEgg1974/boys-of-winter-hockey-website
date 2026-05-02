"""Heuristic playoff series win probabilities for the standings bracket modal.

Combines regular-season pace (points + goal differential per game, aligned with
``postseason_odds``), season head-to-head goal margin in non-playoff games, the
current series score, and an exact best-of-7 Markov calculation from the current
wins (constant per-game win probability). Estimates only — not betting advice.
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select

from app.models import Game, Team, TeamStanding


PREDICTION_METHOD_NOTE = (
    "Blend of RS points pace + goal diff (like postseason sim), season head-to-head "
    "goal margin in non-playoff games, current series lead, and best-of-7 math."
)


def _is_regular_season_game(game_type: str | None) -> bool:
    if game_type is None or not str(game_type).strip():
        return True
    t = str(game_type).strip().lower()
    if any(
        x in t
        for x in (
            "playoff",
            "play-off",
            "postseason",
            "post-season",
            "stanley",
        )
    ):
        return False
    if t in ("po", "p", "playoffs"):
        return False
    if "pre" in t or "exhibition" in t:
        return False
    return True


def load_rs_strength_by_team(session, season_id: int) -> dict[int, dict[str, float]]:
    """Per-team RS standing totals used for a simple strength rating."""
    rows = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    out: dict[int, dict[str, float]] = {}
    for st in rows:
        tid = int(st.team_id)
        gp = max(int(st.standing_gp_display() or 0), 1)
        pts = float(st.pts or 0)
        gf = float(st.gf or 0)
        ga = float(st.ga or 0)
        out[tid] = {
            "gp": float(gp),
            "pts": pts,
            "gf": gf,
            "ga": ga,
            "pts_rate": pts / gp,
            "gd_rate": (gf - ga) / gp,
        }
    return out


def load_rs_head_to_head(session, season_id: int) -> dict[tuple[int, int], tuple[int, int, int]]:
    """Sorted (low_id, high_id) -> (goals for low, goals for high, games played)."""
    from collections import defaultdict

    games = session.scalars(
        select(Game).where(Game.season_id == season_id, Game.status == "final")
    ).all()
    agg: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in games:
        if not _is_regular_season_game(g.game_type):
            continue
        if g.home_score is None or g.away_score is None:
            continue
        ha, hb = int(g.home_team_id), int(g.away_team_id)
        hs, aws = int(g.home_score), int(g.away_score)
        lo, hi = (ha, hb) if ha < hb else (hb, ha)
        key = (lo, hi)
        if g.home_team_id == lo:
            agg[key][0] += hs
            agg[key][1] += aws
        else:
            agg[key][0] += aws
            agg[key][1] += hs
        agg[key][2] += 1
    return {k: (v[0], v[1], v[2]) for k, v in agg.items()}


def _team_rating(rs_map: dict[int, dict[str, float]], tid: int) -> float:
    """Same shape as ``postseason_odds._rating`` for comparable scale."""
    m = rs_map.get(tid)
    if not m:
        return 55.0
    gp = max(int(m["gp"]), 1)
    pace = float(m["pts"]) / gp
    gd = (float(m["gf"]) - float(m["ga"])) / gp
    return pace * 55.0 + gd * 1.8


def _logistic_stable(x: float) -> float:
    if x >= 0.0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _h2h_per_game_margin_for_team_a(
    h2h: dict[tuple[int, int], tuple[int, int, int]],
    tid_a: int,
    tid_b: int,
) -> float:
    lo, hi = (tid_a, tid_b) if tid_a < tid_b else (tid_b, tid_a)
    row = h2h.get((lo, hi))
    if not row or row[2] <= 0:
        return 0.0
    gf_lo, gf_hi, n = row
    margin = (gf_lo - gf_hi) if tid_a == lo else (gf_hi - gf_lo)
    return float(margin) / float(n)


def _series_win_prob_from_state(wa: int, wb: int, p_a_wins_game: float) -> float:
    """P(team A wins the series) from current (wa, wb) with i.i.d. games."""
    p = min(0.97, max(0.03, float(p_a_wins_game)))
    memo: dict[tuple[int, int], float] = {}

    def dp(a: int, b: int) -> float:
        if a >= 4:
            return 1.0
        if b >= 4:
            return 0.0
        k = (a, b)
        if k in memo:
            return memo[k]
        memo[k] = p * dp(a + 1, b) + (1.0 - p) * dp(a, b + 1)
        return memo[k]

    return dp(int(wa), int(wb))


def matchup_prediction_dict(
    *,
    team_a_id: int,
    team_b_id: int,
    wins_a: int,
    wins_b: int,
    rs_map: dict[int, dict[str, float]],
    h2h: dict[tuple[int, int], tuple[int, int, int]],
    teams: dict[int, Team],
) -> dict[str, Any] | None:
    if wins_a >= 4 or wins_b >= 4:
        return None
    ra = _team_rating(rs_map, team_a_id)
    rb = _team_rating(rs_map, team_b_id)
    z = (ra - rb) * 0.11
    z += 0.45 * float(int(wins_a) - int(wins_b))
    z += 0.12 * _h2h_per_game_margin_for_team_a(h2h, team_a_id, team_b_id)
    p_game_a = _logistic_stable(z)
    p_game_a = min(0.9, max(0.1, p_game_a))
    p_a_series = _series_win_prob_from_state(int(wins_a), int(wins_b), p_game_a)
    fav_id = team_a_id if p_a_series >= 0.5 else team_b_id
    p_fav = p_a_series if fav_id == team_a_id else (1.0 - p_a_series)
    ta = teams.get(team_a_id)
    tb = teams.get(team_b_id)
    fav = teams.get(fav_id)
    return {
        "favorite_team_id": fav_id,
        "favorite": _team_mini(fav),
        "favorite_win_series": round(float(p_fav), 4),
        "team_a_win_series": round(float(p_a_series), 4),
        "p_game_team_a": round(float(p_game_a), 4),
        "team_a": _team_mini(ta),
        "team_b": _team_mini(tb),
        "method_note": PREDICTION_METHOD_NOTE,
    }


def _team_mini(t: Team | None) -> dict[str, Any] | None:
    if not t:
        return None
    return {
        "id": t.id,
        "abbreviation": (t.abbreviation or "").strip(),
        "name": (t.name or "").strip(),
    }
