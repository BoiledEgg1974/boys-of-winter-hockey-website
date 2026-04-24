"""Monte Carlo postseason odds for the homepage (playoffs through championship).

Uses remaining regular-season games (when present) plus a simplified best-of-7
playoff bracket. Estimates only — see ``method_note`` in the JSON payload.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.models import Game, Team, TeamStanding
from app.services.playoff_bracket import is_playoff_game_type


def _is_regular_season_game(game_type: str | None) -> bool:
    if game_type is None or not str(game_type).strip():
        return True
    t = str(game_type).strip().lower()
    if is_playoff_game_type(game_type):
        return False
    if "pre" in t or "exhibition" in t:
        return False
    return True


def _series_win_probability(p_game: float) -> float:
    """P(team A wins best-of-7) given p_game = P(A wins one game), i.i.d."""
    p = min(0.999, max(0.001, float(p_game)))
    memo: dict[tuple[int, int], float] = {}

    def dp(wa: int, wb: int) -> float:
        if wa >= 4:
            return 1.0
        if wb >= 4:
            return 0.0
        key = (wa, wb)
        if key in memo:
            return memo[key]
        memo[key] = p * dp(wa + 1, wb) + (1.0 - p) * dp(wa, wb + 1)
        return memo[key]

    return dp(0, 0)


def _tie_tuple(pts: int, w: int, sow: int, gf: int, ga: int) -> tuple[int, int, int, int]:
    row = max(int(w or 0) - int(sow or 0), 0)
    gd = int(gf or 0) - int(ga or 0)
    return (int(pts or 0), row, gd, int(gf or 0))


@dataclass
class _SimTeam:
    team_id: int
    conference: str
    division: str
    gp: int
    pts: int
    w: int
    sow: int
    gf: int
    ga: int


def _rating(st: _SimTeam) -> float:
    gp = max(int(st.gp or 0), 1)
    pace = float(st.pts) / gp
    gd = (float(st.gf) - float(st.ga)) / gp
    return pace * 55.0 + gd * 1.8


def _logistic_stable(x: float) -> float:
    """``1 / (1 + exp(-x))`` without overflow when ``|x|`` is large."""
    if x >= 0.0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _p_home_win(h: _SimTeam, a: _SimTeam, rng: random.Random) -> float:
    rh = _rating(h)
    ra = _rating(a)
    diff = rh - ra + 0.42
    p = _logistic_stable(diff * 0.11)
    return min(0.92, max(0.08, p))


def _play_series(a: _SimTeam, b: _SimTeam, rng: random.Random) -> int:
    ra, rb = _rating(a), _rating(b)
    p_a = _logistic_stable((ra - rb) * 0.12)
    p_a = min(0.88, max(0.12, p_a))
    return a.team_id if rng.random() < _series_win_probability(p_a) else b.team_id


def _nhl_style_qualifiers(conf_teams: list[_SimTeam]) -> set[int]:
    if not conf_teams:
        return set()
    n = len(conf_teams)
    if n <= 8:
        return {t.team_id for t in conf_teams}
    by_div: dict[str, list[_SimTeam]] = defaultdict(list)
    for t in conf_teams:
        dk = (t.division or "").strip() or "League"
        by_div[dk].append(t)
    qualified: set[int] = set()
    multi_div = len(by_div) >= 2 and all(len(v) >= 3 for v in by_div.values())
    if multi_div:
        for _div, div_ts in by_div.items():
            div_ts = sorted(div_ts, key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga), reverse=True)
            for t in div_ts[:3]:
                qualified.add(t.team_id)
        rest = [t for t in conf_teams if t.team_id not in qualified]
        rest.sort(key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga), reverse=True)
        for t in rest[:2]:
            qualified.add(t.team_id)
    if len(qualified) < 8:
        pool = [t for t in conf_teams if t.team_id not in qualified]
        pool.sort(key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga), reverse=True)
        for t in pool:
            qualified.add(t.team_id)
            if len(qualified) >= 8:
                break
    return qualified


def _bracket_champion_and_runnerup(rows: list[_SimTeam], rng: random.Random) -> tuple[int | None, int | None]:
    """8-team seeded bracket; returns (champion_id, runner_up_id)."""
    qual_ids = _nhl_style_qualifiers(rows)
    if not qual_ids:
        return None, None
    by_id = {t.team_id: t for t in rows}
    qual = [by_id[i] for i in qual_ids if i in by_id]
    qual.sort(key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga), reverse=True)
    rnd: list[_SimTeam] = qual[:8]
    if len(rnd) < 2:
        return (rnd[0].team_id, None) if rnd else (None, None)
    while len(rnd) > 2:
        next_r: list[_SimTeam] = []
        for i in range(0, len(rnd), 2):
            if i + 1 >= len(rnd):
                next_r.append(rnd[i])
            else:
                w = _play_series(rnd[i], rnd[i + 1], rng)
                next_r.append(rnd[i] if rnd[i].team_id == w else rnd[i + 1])
        rnd = next_r
    a, b = rnd[0], rnd[1]
    w = _play_series(a, b, rng)
    if w == a.team_id:
        return a.team_id, b.team_id
    return b.team_id, a.team_id


def _regulation_goals(home_won: bool, rng: random.Random) -> tuple[int, int]:
    """Return (home_goals, away_goals) for a regulation decision."""
    if home_won:
        hg = int(rng.randint(2, 6))
        ag = int(rng.randint(0, min(3, hg - 1)))
    else:
        ag = int(rng.randint(2, 6))
        hg = int(rng.randint(0, min(3, ag - 1)))
    return hg, ag


def _simulate_rs_game(
    home: _SimTeam,
    away: _SimTeam,
    pts: dict[int, int],
    w: dict[int, int],
    gf: dict[int, int],
    ga: dict[int, int],
    rng: random.Random,
) -> None:
    h, a = home, away
    p = _p_home_win(h, a, rng)
    otl = rng.random() < 0.235
    hid, aid = h.team_id, a.team_id
    if otl:
        if rng.random() < p:
            pts[hid] = pts.get(hid, 0) + 2
            pts[aid] = pts.get(aid, 0) + 1
            w[hid] = w.get(hid, 0) + 1
            gf[hid] += 2
            ga[hid] += 1
            gf[aid] += 1
            ga[aid] += 2
        else:
            pts[aid] = pts.get(aid, 0) + 2
            pts[hid] = pts.get(hid, 0) + 1
            w[aid] = w.get(aid, 0) + 1
            gf[aid] += 2
            ga[aid] += 1
            gf[hid] += 1
            ga[hid] += 2
    else:
        if rng.random() < p:
            pts[hid] = pts.get(hid, 0) + 2
            w[hid] = w.get(hid, 0) + 1
            hg, ag = _regulation_goals(True, rng)
            gf[hid] += hg
            ga[hid] += ag
            gf[aid] += ag
            ga[aid] += hg
        else:
            pts[aid] = pts.get(aid, 0) + 2
            w[aid] = w.get(aid, 0) + 1
            hg, ag = _regulation_goals(False, rng)
            gf[hid] += hg
            ga[hid] += ag
            gf[aid] += ag
            ga[aid] += hg


def _division_winners(rows: list[_SimTeam]) -> set[int]:
    key_groups: dict[tuple[str, str], list[_SimTeam]] = defaultdict(list)
    for t in rows:
        ck = (t.conference or "").strip() or "League"
        dk = (t.division or "").strip() or "League"
        key_groups[(ck, dk)].append(t)
    out: set[int] = set()
    for _k, group in key_groups.items():
        if not group:
            continue
        best = max(group, key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga))
        tops = [
            x
            for x in group
            if _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga) == _tie_tuple(best.pts, best.w, best.sow, best.gf, best.ga)
        ]
        if len(tops) == 1:
            out.add(tops[0].team_id)
    return out


def _boiled_egg_winners(rows: list[_SimTeam]) -> set[int]:
    if not rows:
        return set()
    best = max(rows, key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga))
    tk = _tie_tuple(best.pts, best.w, best.sow, best.gf, best.ga)
    return {x.team_id for x in rows if _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga) == tk}


def _single_monte_draw(
    rng: random.Random,
    base_rows: list[_SimTeam],
    base_by_id: dict[int, _SimTeam],
    remaining: list[tuple[int, int]],
    two_conf: bool,
    team_ids: list[int],
    counts: dict[int, dict[str, int]],
    trace_tid: int | None,
    trace_pts: list[int] | None,
    trace_gf: list[int] | None,
    trace_ga: list[int] | None,
) -> None:
    pts = {t.team_id: t.pts for t in base_rows}
    w = {t.team_id: t.w for t in base_rows}
    gf = {t.team_id: t.gf for t in base_rows}
    ga = {t.team_id: t.ga for t in base_rows}
    for hid, aid in remaining:
        bh, ba = base_by_id.get(hid), base_by_id.get(aid)
        if not bh or not ba:
            continue
        h = _SimTeam(
            bh.team_id,
            bh.conference,
            bh.division,
            bh.gp,
            pts[hid],
            w[hid],
            bh.sow,
            bh.gf,
            bh.ga,
        )
        a = _SimTeam(
            ba.team_id,
            ba.conference,
            ba.division,
            ba.gp,
            pts[aid],
            w[aid],
            ba.sow,
            ba.gf,
            ba.ga,
        )
        _simulate_rs_game(h, a, pts, w, gf, ga, rng)

    sim_teams = [
        _SimTeam(
            team_id=t.team_id,
            conference=t.conference,
            division=t.division,
            gp=t.gp,
            pts=pts[t.team_id],
            w=w[t.team_id],
            sow=t.sow,
            gf=gf[t.team_id],
            ga=ga[t.team_id],
        )
        for t in base_rows
    ]
    qual_all: set[int] = set()
    conf_champs: dict[str, tuple[int, int | None]] = {}
    by_conf: dict[str, list[_SimTeam]] = defaultdict(list)
    for t in sim_teams:
        by_conf[t.conference].append(t)
    for _ck, rows_c in by_conf.items():
        if len(rows_c) < 4:
            qual_all |= {x.team_id for x in rows_c}
            continue
        q = _nhl_style_qualifiers(rows_c)
        qual_all |= q
        champ, runner = _bracket_champion_and_runnerup(rows_c, rng)
        if champ is not None:
            conf_champs[_ck] = (champ, runner)

    for tid in qual_all:
        if tid in counts:
            counts[tid]["playoffs"] += 1

    for tid in _division_winners(sim_teams):
        if tid in counts:
            counts[tid]["division"] += 1

    for tid in _boiled_egg_winners(sim_teams):
        if tid in counts:
            counts[tid]["boiledegg"] += 1

    post_by_id = {t.team_id: t for t in sim_teams}
    if two_conf and len(conf_champs) >= 2:
        ranked = sorted(by_conf.items(), key=lambda kv: -len(kv[1]))
        c1, c2 = ranked[0][0], ranked[1][0]
        p1 = conf_champs.get(c1)
        p2 = conf_champs.get(c2)
        if p1 and p2:
            id1, _ru1 = p1
            id2, _ru2 = p2
            t1, t2 = post_by_id.get(id1), post_by_id.get(id2)
            if t1 and t2:
                if id1 in counts:
                    counts[id1]["conference"] += 1
                if id2 in counts:
                    counts[id2]["conference"] += 1
                cup_w = _play_series(t1, t2, rng)
                if cup_w in counts:
                    counts[cup_w]["bowl_championship"] += 1
    else:
        overall = sorted(sim_teams, key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga), reverse=True)[:8]
        if len(overall) >= 2:
            champ, runner = _bracket_champion_and_runnerup(overall, rng)
            if champ is not None and champ in counts:
                counts[champ]["bowl_championship"] += 1
            if champ is not None and champ in counts:
                counts[champ]["conference"] += 1
            if runner is not None and runner in counts:
                counts[runner]["conference"] += 1

    if trace_tid is not None and trace_pts is not None and trace_gf is not None and trace_ga is not None:
        if trace_tid in pts:
            trace_pts.append(int(pts[trace_tid]))
            trace_gf.append(int(gf[trace_tid]))
            trace_ga.append(int(ga[trace_tid]))


def _load_monte_carlo_context(
    session, season_id: int, teams_by_id: dict[int, Team]
) -> tuple[list[_SimTeam], list[tuple[int, int]], dict[int, _SimTeam], bool, list[int]] | None:
    standings = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    if not standings:
        return None
    base_rows: list[_SimTeam] = []
    for st in standings:
        tm = teams_by_id.get(st.team_id)
        if not tm:
            continue
        conf = (st.conference or "").strip() or "League"
        div = (st.division or "").strip() or "League"
        gp = int(st.standing_gp_display() or 0)
        base_rows.append(
            _SimTeam(
                team_id=st.team_id,
                conference=conf,
                division=div,
                gp=max(gp, 1),
                pts=int(st.pts or 0),
                w=int(st.w or 0),
                sow=int(st.shootout_wins or 0),
                gf=int(st.gf or 0),
                ga=int(st.ga or 0),
            )
        )
    if not base_rows:
        return None

    games = session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status != "final",
            Game.home_team_id.is_not(None),
            Game.away_team_id.is_not(None),
        )
    ).all()
    remaining: list[tuple[int, int]] = []
    for g in games:
        if _is_regular_season_game(g.game_type):
            remaining.append((int(g.home_team_id), int(g.away_team_id)))

    team_ids = [t.team_id for t in base_rows]
    base_by_id = {t.team_id: t for t in base_rows}
    conf_sizes: dict[str, int] = defaultdict(int)
    for t in base_rows:
        conf_sizes[t.conference] += 1
    two_conf = sum(1 for sz in conf_sizes.values() if sz >= 8) >= 2
    return base_rows, remaining, base_by_id, two_conf, team_ids


def _percentile_int(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    n = len(sorted_vals)
    idx = int(round((n - 1) * p))
    idx = max(0, min(n - 1, idx))
    return int(sorted_vals[idx])


def _fmt_pct_label(p: float) -> str:
    x = p * 100.0
    if x > 0.0 and x < 0.1:
        return "<0.1%"
    return f"{x:.1f}%"


def build_postseason_odds_payload(
    session,
    season_id: int,
    teams_by_id: dict[int, Team],
    *,
    n_sims: int = 600,
) -> dict[str, Any] | None:
    ctx = _load_monte_carlo_context(session, season_id, teams_by_id)
    if ctx is None:
        return None
    base_rows, remaining, base_by_id, two_conf, team_ids = ctx

    counts = {tid: {"playoffs": 0, "division": 0, "conference": 0, "boiledegg": 0, "bowl_championship": 0} for tid in team_ids}

    for sim_i in range(n_sims):
        rng = random.Random(sim_i * 1_000_003 + season_id * 17)
        _single_monte_draw(
            rng,
            base_rows,
            base_by_id,
            remaining,
            two_conf,
            team_ids,
            counts,
            None,
            None,
            None,
            None,
        )

    by_slug: dict[str, dict[str, float]] = {}
    teams_payload: list[dict[str, str]] = []
    for tid in sorted(team_ids, key=lambda i: (teams_by_id[i].name or "").lower()):
        tm = teams_by_id[tid]
        slug = tm.slug or str(tid)
        teams_payload.append(
            {
                "slug": slug,
                "abbr": tm.abbreviation or "",
                "name": tm.full_display_name(),
            }
        )
        c = counts[tid]
        by_slug[slug] = {
            "playoffs": c["playoffs"] / n_sims,
            "division": c["division"] / n_sims,
            "conference": c["conference"] / n_sims,
            "boiledegg": c["boiledegg"] / n_sims,
            "bowl_championship": c["bowl_championship"] / n_sims,
        }

    leader = max(base_rows, key=lambda x: _tie_tuple(x.pts, x.w, x.sow, x.gf, x.ga))
    leader_tm = teams_by_id.get(leader.team_id)
    default_slug = leader_tm.slug if leader_tm and leader_tm.slug else None

    return {
        "n_sims": n_sims,
        "remaining_rs_games": len(remaining),
        "default_slug": default_slug,
        "teams": teams_payload,
        "by_slug": by_slug,
        "method_note": (
            f"Estimated from {n_sims} Monte Carlo samples: remaining regular-season games "
            "(when scheduled) use team pace and home ice; playoffs use a simplified best-of-7 "
            "bracket. Not an official projection."
        ),
    }


def build_team_page_mc_bundle(
    session,
    season_id: int,
    team_id: int,
    teams_by_id: dict[int, Team],
    *,
    n_sims: int = 800,
) -> dict[str, Any] | None:
    """Season projection + postseason odds for one team (team schedule page)."""
    ctx = _load_monte_carlo_context(session, season_id, teams_by_id)
    if ctx is None:
        return None
    base_rows, remaining, base_by_id, two_conf, team_ids = ctx
    if team_id not in team_ids:
        return None

    counts = {tid: {"playoffs": 0, "division": 0, "conference": 0, "boiledegg": 0, "bowl_championship": 0} for tid in team_ids}
    trace_pts: list[int] = []
    trace_gf: list[int] = []
    trace_ga: list[int] = []

    for sim_i in range(n_sims):
        rng = random.Random(sim_i * 1_000_003 + season_id * 19 + team_id * 3)
        _single_monte_draw(
            rng,
            base_rows,
            base_by_id,
            remaining,
            two_conf,
            team_ids,
            counts,
            team_id,
            trace_pts,
            trace_gf,
            trace_ga,
        )

    if not trace_pts:
        return None

    sv = sorted(trace_pts)
    mean_pts = sum(trace_pts) / len(trace_pts)
    mean_gf = sum(trace_gf) / len(trace_gf)
    mean_ga = sum(trace_ga) / len(trace_ga)
    c = counts[team_id]
    note = (
        f"Estimated from {n_sims} Monte Carlo samples (same methodology as league home). "
        "Not an official projection."
    )
    return {
        "n_sims": n_sims,
        "remaining_rs_games": len(remaining),
        "method_note": note,
        "projection": {
            "mean_pts": round(mean_pts, 1),
            "pts_p05": _percentile_int(sv, 0.05),
            "pts_p95": _percentile_int(sv, 0.95),
            "mean_gf": round(mean_gf, 1),
            "mean_ga": round(mean_ga, 1),
        },
        "postseason": {
            "playoffs": c["playoffs"] / n_sims,
            "division": c["division"] / n_sims,
            "conference": c["conference"] / n_sims,
            "boiledegg": c["boiledegg"] / n_sims,
            "bowl_championship": c["bowl_championship"] / n_sims,
        },
        "postseason_labels": {
            "playoffs": _fmt_pct_label(c["playoffs"] / n_sims),
            "division": _fmt_pct_label(c["division"] / n_sims),
            "conference": _fmt_pct_label(c["conference"] / n_sims),
            "boiledegg": _fmt_pct_label(c["boiledegg"] / n_sims),
            "bowl_championship": _fmt_pct_label(c["bowl_championship"] / n_sims),
        },
    }
