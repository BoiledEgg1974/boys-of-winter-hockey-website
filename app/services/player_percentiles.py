"""League percentile analytics card (BOWL WAR and component metrics from FHM exports)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, PlayerGoalieStat, PlayerSkaterStat, Season, Team
from app.services.advanced_stats import (
    MIN_GOALIE_GP,
    MIN_SKATER_GP,
    MIN_SKATER_TOI_SECONDS,
    SQ_KEYS,
    _adaptive_min_gp,
    _estimated_goalie_gsaa,
    _league_goalie_sv_pct,
    _player_pp_pts_per_60,
    _player_sh_pts_per_60,
    sq_profile_from_counts,
)
from app.services.player_analytics import _linemates_from_assignments, _load_team_lines

_SHOOTING_LUCK_RATE = 0.09

_BOWL_WAR_WEIGHTS: dict[str, float] = {
    "gf_per_60": 0.18,
    "ga_per_60": 0.14,
    "cf_pct_rel": 0.12,
    "game_rating_off": 0.16,
    "game_rating_def": 0.14,
    "pp_pts_per_60": 0.08,
    "sh_pts_per_60": 0.06,
    "finishing": 0.12,
}

_GOALIE_WAR_WEIGHTS: dict[str, float] = {
    "sv_pct": 0.20,
    "gsaa": 0.18,
    "gaa": 0.12,
    "game_rating": 0.20,
    "minutes": 0.08,
    "quality_start_pct": 0.08,
    "excellent_start_pct": 0.06,
    "bad_start_pct": 0.04,
    "consistency": 0.04,
}

_GOALIE_GRID_KEYS: tuple[tuple[str, str, bool], ...] = (
    ("Save %", "sv_pct", True),
    ("GSAA", "gsaa", True),
    ("GAA", "gaa", False),
    ("Game Rating", "game_rating", True),
    ("Workload", "minutes", True),
    ("Quality Starts", "quality_start_pct", True),
    ("Excellent Starts", "excellent_start_pct", True),
    ("Bad Starts", "bad_start_pct", False),
    ("Rebound Skill", "rebound_rating", True),
    ("Consistency", "consistency", True),
)


@dataclass(frozen=True)
class _SkaterMetricRow:
    player_id: int
    position_group: str
    gp: int
    toi_seconds: int
    metrics: dict[str, float | None]


@dataclass(frozen=True)
class _GoalieMetricRow:
    player_id: int
    gp: int
    metrics: dict[str, float | None]


def _position_group(position: str | None, *, is_goalie: bool = False) -> str:
    if is_goalie:
        return "goalie"
    pos = (position or "").strip().upper()
    if pos in {"D", "LD", "RD", "DEF", "DEFENSE", "DEFENCE"} or pos.startswith("D"):
        return "defense"
    return "forward"


def _season_short_label(season: Season | None) -> str:
    if not season or season.start_year is None:
        return "—"
    end = season.end_year or (int(season.start_year) + 1)
    return f"{int(season.start_year) % 100:02d}-{end % 100:02d}"


def percentile_int(value: float | None, pool: list[float], *, higher_is_better: bool = True) -> int | None:
    if value is None or not pool:
        return None
    n = len(pool)
    if n == 1:
        return 50
    if higher_is_better:
        below = sum(1 for v in pool if v < value)
        equal = sum(1 for v in pool if v == value)
    else:
        below = sum(1 for v in pool if v > value)
        equal = sum(1 for v in pool if v == value)
    # Tie-aware rank; cap at 99 so no player shows a perfect 100th percentile.
    rank = below + (equal + 1) / 2.0
    pct = 100.0 * rank / (n + 1)
    return max(0, min(99, int(round(pct))))


def _pct_tier(pct: int | None) -> str:
    if pct is None:
        return "empty"
    if pct >= 75:
        return "high"
    if pct >= 40:
        return "mid"
    return "low"


def _display_pct(pct: int | None) -> str:
    return f"{pct}%" if pct is not None else "—"


_SKATER_GRID_KEYS: tuple[tuple[str, str, bool], ...] = (
    ("EV Offence", "game_rating_off", True),
    ("EV Defence", "game_rating_def", True),
    ("PP", "pp_pts_per_60", True),
    ("PK", "sh_pts_per_60", True),
    ("Finishing", "finishing", True),
    ("Goals", "goals", True),
    ("Penalties", "pim", False),
    ("Competition", "competition", True),
    ("Teammates", "teammates", True),
)

_MIN_PERCENTILE_POOL = 2
_MIN_FINISHING_SHOTS = 20
_MIN_GOALIE_STARTS_LOG = 5


def finishing_value(goals: int | None, shots: int | None) -> float | None:
    if goals is None or shots is None:
        return None
    return float(goals) - float(shots) * _SHOOTING_LUCK_RATE


def _war_pct_from_metrics(metrics: dict[str, float | None], pools: dict[str, list[float]]) -> int | None:
    raw = bowl_war_raw(metrics, pools)
    if raw is None:
        return None
    return max(0, min(99, int(round(raw))))


def bowl_war_raw(metrics: dict[str, float | None], pools: dict[str, list[float]]) -> float | None:
    parts: list[float] = []
    weights: list[float] = []
    for key, weight in _BOWL_WAR_WEIGHTS.items():
        val = metrics.get(key)
        pool = pools.get(key) or []
        if val is None or len(pool) < _MIN_PERCENTILE_POOL:
            continue
        higher = key != "ga_per_60"
        pct = percentile_int(val, pool, higher_is_better=higher)
        if pct is None:
            continue
        parts.append(float(pct) * weight)
        weights.append(weight)
    if not weights:
        gr = metrics.get("game_rating")
        return float(gr) if gr is not None else None
    return sum(parts) / sum(weights)


def bowl_goalie_war_raw(metrics: dict[str, float | None], pools: dict[str, list[float]]) -> float | None:
    parts: list[float] = []
    weights: list[float] = []
    for key, weight in _GOALIE_WAR_WEIGHTS.items():
        val = metrics.get(key)
        pool = pools.get(key) or []
        if val is None or len(pool) < _MIN_PERCENTILE_POOL:
            continue
        higher = key != "gaa" and key != "bad_start_pct"
        pct = percentile_int(val, pool, higher_is_better=higher)
        if pct is None:
            continue
        parts.append(float(pct) * weight)
        weights.append(weight)
    if not weights:
        gr = metrics.get("game_rating")
        return float(gr) if gr is not None else None
    return sum(parts) / sum(weights)


def _goalie_war_pct_from_metrics(metrics: dict[str, float | None], pools: dict[str, list[float]]) -> int | None:
    raw = bowl_goalie_war_raw(metrics, pools)
    if raw is None:
        return None
    return max(0, min(99, int(round(raw))))


def chart_svg(
    labels: list[str],
    series: list[dict[str, Any]],
    *,
    width: int | None = None,
    height: int = 112,
    ymin: float = 0.0,
    ymax: float = 100.0,
) -> dict[str, Any]:
    n = len(labels)
    if n < 1:
        return {
            "has_data": False,
            "width": width or 240,
            "height": height,
            "paths": [],
            "x_labels": [],
            "y_labels": [],
            "grid_lines": [],
        }
    pad_l, pad_r, pad_t, pad_b = 30, 8, 8, 22
    if width is None:
        width = max(240, pad_l + pad_r + max(0, (n - 1) * 28))
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    span = ymax - ymin or 1.0

    def x_at(i: int) -> float:
        if n == 1:
            return pad_l + inner_w / 2.0
        return pad_l + (i * inner_w / (n - 1))

    def y_at(v: float) -> float:
        return pad_t + inner_h - ((v - ymin) / span) * inner_h

    y_ticks = (0, 25, 50, 75, 100)
    y_labels = [{"x": 2, "y": y_at(float(v)) + 3, "text": f"{v}%"} for v in y_ticks]
    grid_lines = [{"x1": pad_l, "x2": width - pad_r, "y": y_at(float(v))} for v in y_ticks]
    x_labels = [{"x": x_at(i), "y": height - 4, "text": labels[i]} for i in range(n)]

    paths: list[dict[str, Any]] = []
    for s in series:
        vals = s.get("values") or []
        pts: list[tuple[int, float, float]] = []
        for i, v in enumerate(vals):
            if v is None:
                continue
            pts.append((i, x_at(i), y_at(float(v))))
        if not pts:
            continue
        d = None
        if len(pts) >= 2:
            d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for _i, x, y in pts)
        dots: list[dict[str, Any]] = []
        for j, (_i, x, y) in enumerate(pts):
            dots.append(
                {
                    "cx": round(x, 1),
                    "cy": round(y, 1),
                    "class": s.get("class") or "player-analytics-card__chart-line",
                    "highlight": j == 0 or j == len(pts) - 1,
                }
            )
        paths.append(
            {
                "d": d,
                "class": s.get("class") or "player-analytics-card__chart-line",
                "stroke_dasharray": s.get("stroke_dasharray"),
                "dots": dots,
            }
        )
    return {
        "has_data": bool(paths),
        "width": width,
        "height": height,
        "paths": paths,
        "x_labels": x_labels,
        "y_labels": y_labels,
        "grid_lines": grid_lines,
    }


def _aggregate_sq_for_player(session: Session, player_id: int, season_id: int) -> dict[str, int]:
    rows = session.execute(
        select(
            GameSkaterStat.sq0,
            GameSkaterStat.sq1,
            GameSkaterStat.sq2,
            GameSkaterStat.sq3,
            GameSkaterStat.sq4,
        )
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(
            GameSkaterStat.player_id == player_id,
            Game.season_id == season_id,
            Game.status == "final",
        )
    ).all()
    counts = {k: 0 for k in SQ_KEYS}
    for row in rows:
        for i, k in enumerate(SQ_KEYS):
            counts[k] += int(row[i] or 0)
    return counts


def _teammate_quality_gr(
    session: Session,
    *,
    season_id: int,
    segment: str,
    team_fhm: int | None,
    player_fhm: str,
    raw_dir: Path,
) -> float | None:
    if not team_fhm or not player_fhm:
        return None
    team_lines = _load_team_lines(raw_dir)
    mates = _linemates_from_assignments(team_lines, team_fhm, player_fhm, session=session)
    if not mates:
        return None
    fhm_ids = [str(m.get("fhm_player_id") or "").strip() for m in mates]
    fhm_ids = [x for x in fhm_ids if x]
    if not fhm_ids:
        return None
    players = session.scalars(select(Player).where(Player.fhm_player_id.in_(fhm_ids))).all()
    pid_by_fhm = {str(p.fhm_player_id): p.id for p in players}
    ratings: list[float] = []
    for fid in fhm_ids:
        pid = pid_by_fhm.get(fid)
        if pid is None:
            continue
        gr = session.scalar(
            select(PlayerSkaterStat.game_rating).where(
                PlayerSkaterStat.player_id == pid,
                PlayerSkaterStat.season_id == season_id,
                PlayerSkaterStat.stat_segment == segment,
            ).limit(1)
        )
        if gr is not None:
            ratings.append(float(gr))
    if not ratings:
        return None
    return sum(ratings) / len(ratings)


def _opponent_quality_gr(
    session: Session,
    *,
    player_id: int,
    season_id: int,
    segment: str,
    team_id: int | None,
) -> float | None:
    """QoC proxy: mean opponent skater game rating in games this player dressed."""
    if not team_id:
        return None
    game_rows = session.execute(
        select(Game.home_team_id, Game.away_team_id)
        .join(GameSkaterStat, GameSkaterStat.game_id == Game.id)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            GameSkaterStat.player_id == player_id,
        )
        .distinct()
    ).all()
    if not game_rows:
        return None
    opp_avg_cache: dict[int, float] = {}
    samples: list[float] = []
    for home_id, away_id in game_rows:
        opp_id = away_id if home_id == team_id else home_id if away_id == team_id else None
        if opp_id is None:
            continue
        if opp_id not in opp_avg_cache:
            avg = session.scalar(
                select(func.avg(PlayerSkaterStat.game_rating)).where(
                    PlayerSkaterStat.season_id == season_id,
                    PlayerSkaterStat.stat_segment == segment,
                    PlayerSkaterStat.team_id == opp_id,
                    PlayerSkaterStat.game_rating.isnot(None),
                    PlayerSkaterStat.gp > 0,
                )
            )
            if avg is None:
                continue
            opp_avg_cache[opp_id] = float(avg)
        samples.append(opp_avg_cache[opp_id])
    if not samples:
        return None
    return sum(samples) / len(samples)


def _skater_metric_row(
    session: Session,
    st: PlayerSkaterStat,
    *,
    season_id: int,
    segment: str,
    raw_dir: Path,
) -> _SkaterMetricRow:
    player = st.player
    pos_group = _position_group(player.position if player else None)
    sq = sq_profile_from_counts(_aggregate_sq_for_player(session, st.player_id, season_id))
    finishing = finishing_value(st.goals, st.shots)
    team_fhm = None
    if st.team and st.team.fhm_team_id:
        try:
            team_fhm = int(str(st.team.fhm_team_id).strip())
        except (TypeError, ValueError):
            team_fhm = None
    teammate_gr = _teammate_quality_gr(
        session,
        season_id=season_id,
        segment=segment,
        team_fhm=team_fhm,
        player_fhm=str(player.fhm_player_id or "") if player else "",
        raw_dir=raw_dir,
    )
    competition_gr = _opponent_quality_gr(
        session,
        player_id=st.player_id,
        season_id=season_id,
        segment=segment,
        team_id=st.team_id,
    )
    pim_per_60 = None
    if st.toi_seconds and st.toi_seconds > 0 and st.pim is not None:
        pim_per_60 = float(st.pim) / (st.toi_seconds / 3600.0)
    goals_per_60 = None
    if st.toi_seconds and st.toi_seconds > 0 and st.goals is not None:
        goals_per_60 = float(st.goals) / (st.toi_seconds / 3600.0)

    metrics: dict[str, float | None] = {
        "game_rating": float(st.game_rating) if st.game_rating is not None else None,
        "game_rating_off": float(st.game_rating_off) if st.game_rating_off is not None else None,
        "game_rating_def": float(st.game_rating_def) if st.game_rating_def is not None else None,
        "gf_per_60": float(st.gf_per_60) if st.gf_per_60 is not None else None,
        "ga_per_60": float(st.ga_per_60) if st.ga_per_60 is not None else None,
        "cf_pct_rel": float(st.cf_pct_rel) if st.cf_pct_rel is not None else None,
        "pp_pts_per_60": _player_pp_pts_per_60(st),
        "sh_pts_per_60": _player_sh_pts_per_60(st),
        "finishing": finishing,
        "goals": float(st.goals) if st.goals is not None else None,
        "goals_per_60": goals_per_60,
        "pim": float(st.pim) if st.pim is not None else None,
        "pim_per_60": pim_per_60,
        "competition": competition_gr,
        "teammates": teammate_gr,
        "high_danger_share": float(sq["high_danger_share"]) if sq.get("high_danger_share") is not None else None,
    }
    return _SkaterMetricRow(
        player_id=st.player_id,
        position_group=pos_group,
        gp=int(st.gp or 0),
        toi_seconds=int(st.toi_seconds or 0),
        metrics=metrics,
    )


def _qualified_skater_row(row: _SkaterMetricRow, min_gp: int) -> bool:
    return row.gp >= min_gp and row.toi_seconds >= MIN_SKATER_TOI_SECONDS


def _build_skater_pool(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
    position_group: str,
    raw_dir: Path,
) -> list[_SkaterMetricRow]:
    min_gp = _adaptive_min_gp(session, PlayerSkaterStat, season_id, segment, MIN_SKATER_GP)
    stats = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
        .where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
        )
    ).all()
    out: list[_SkaterMetricRow] = []
    for st in stats:
        row = _skater_metric_row(session, st, season_id=season_id, segment=segment, raw_dir=raw_dir)
        if row.position_group != position_group:
            continue
        if not _qualified_skater_row(row, min_gp):
            continue
        out.append(row)
    return out


def _metric_pools(rows: list[_SkaterMetricRow]) -> dict[str, list[float]]:
    keys = set(_BOWL_WAR_WEIGHTS) | {
        "game_rating",
        "game_rating_off",
        "game_rating_def",
        "goals",
        "pim",
        "competition",
        "teammates",
        "pp_pts_per_60",
        "sh_pts_per_60",
        "finishing",
    }
    pools: dict[str, list[float]] = {k: [] for k in keys}
    for row in rows:
        for k in keys:
            v = row.metrics.get(k)
            if v is not None and not math.isnan(v):
                pools[k].append(float(v))
    return pools


def _projected_war_pct(
    current_pct: int | None,
    *,
    age: int | None,
    abi: float | None,
    pot: float | None,
    game_rating: float | None,
) -> int | None:
    if current_pct is None:
        return None
    if age is None or age >= 27 or pot is None or abi is None:
        return min(99, current_pct) if current_pct is not None else None
    if pot <= abi:
        return min(99, current_pct)
    pot_boost = min(12, int(round((pot - abi) * 5)))
    age_boost = max(0, min(6, 24 - age))
    gr_boost = 3 if game_rating is not None and game_rating < 55 else 0
    boosted = current_pct + pot_boost + age_boost + gr_boost
    return min(99, max(current_pct, boosted))


def _normalize_hex_color(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    hx = s[1:] if s.startswith("#") else s
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    if len(hx) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hx):
        return None
    return "#" + hx.upper()


def _team_accent_colors(team: Team | None) -> tuple[str, str]:
    primary = _normalize_hex_color(team.primary_color if team else None) or "#22D3EE"
    secondary = _normalize_hex_color(team.secondary_color if team else None) or primary
    return primary, secondary


def _cap_label(contract: Any | None, years_left: int | None, *, compact: bool = False) -> str:
    if not contract or contract.average_salary is None:
        return "—"
    sal = int(contract.average_salary)
    if sal >= 1_000_000:
        sal_s = f"${sal / 1_000_000:.1f}M".replace(".0M", "M")
    elif compact and sal >= 1_000:
        sal_s = f"${sal / 1_000:.0f}k"
    else:
        sal_s = f"${sal:,}"
    if years_left is not None and years_left > 0:
        return f"{sal_s} x {years_left}"
    return sal_s


def _season_year_label(season: Season | None) -> str | None:
    if not season:
        return None
    if season.end_year is not None:
        return str(int(season.end_year))
    if season.start_year is not None:
        return str(int(season.start_year) + 1)
    return None


def _headline_dict(
    player: Player,
    *,
    photo_url: str | None,
    team_logo_url: str | None,
    team: Team | None,
    proj_war_pct: int | None,
    player_age: int | None,
    role_title: str | None,
    contract: Any | None,
    years_left: int | None,
    is_goalie: bool,
    season_year: str | None = None,
) -> dict[str, Any]:
    team_primary, team_secondary = _team_accent_colors(team)
    return {
        "name": player.full_name,
        "photo_url": photo_url,
        "team_logo_url": team_logo_url,
        "team_name": team.full_display_name() if team else None,
        "team_primary_color": team_primary,
        "team_secondary_color": team_secondary,
        "proj_war_pct": proj_war_pct,
        "position": (player.position or ("G" if is_goalie else "—")).strip(),
        "age": player_age,
        "toi_role": role_title or "—",
        "cap_label": _cap_label(contract, years_left, compact=True),
        "season_year": season_year,
    }


def _empty_skater_grid() -> list[dict[str, Any]]:
    return [
        {"label": label, "display": "—", "pct": None, "tier": "empty"}
        for label, _key, _higher in _SKATER_GRID_KEYS
    ]


def _empty_goalie_grid() -> list[dict[str, Any]]:
    return [
        {"label": label, "display": "—", "pct": None, "tier": "empty"}
        for label, _key, _higher in _GOALIE_GRID_KEYS
    ]


def _threshold_at(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _season_game_gr_thresholds(
    session: Session, season_id: int
) -> tuple[float | None, float | None, float | None]:
    ratings = [
        float(r)
        for r in session.scalars(
            select(GameGoalieStat.game_rating)
            .join(Game, Game.id == GameGoalieStat.game_id)
            .where(
                Game.season_id == season_id,
                Game.status == "final",
                GameGoalieStat.game_rating.isnot(None),
            )
        ).all()
        if r is not None
    ]
    if len(ratings) < 10:
        return None, None, None
    sorted_gr = sorted(ratings)
    return (
        _threshold_at(sorted_gr, 50.0),
        _threshold_at(sorted_gr, 75.0),
        _threshold_at(sorted_gr, 25.0),
    )


def _game_ratings_for_goalie_season(session: Session, player_id: int, season_id: int) -> list[float]:
    return [
        float(r)
        for r in session.scalars(
            select(GameGoalieStat.game_rating)
            .join(Game, Game.id == GameGoalieStat.game_id)
            .where(
                Game.season_id == season_id,
                Game.status == "final",
                GameGoalieStat.player_id == player_id,
                GameGoalieStat.game_rating.isnot(None),
            )
        ).all()
        if r is not None
    ]


def _start_quality_metrics(
    ratings: list[float],
    *,
    median: float | None,
    p75: float | None,
    p25: float | None,
) -> dict[str, float | None]:
    if len(ratings) < _MIN_GOALIE_STARTS_LOG or median is None or p75 is None or p25 is None:
        return {
            "quality_start_pct": None,
            "excellent_start_pct": None,
            "bad_start_pct": None,
        }
    n = len(ratings)
    return {
        "quality_start_pct": 100.0 * sum(1 for r in ratings if r >= median) / n,
        "excellent_start_pct": 100.0 * sum(1 for r in ratings if r >= p75) / n,
        "bad_start_pct": 100.0 * sum(1 for r in ratings if r <= p25) / n,
    }


def _consistency_score(ratings: list[float]) -> float | None:
    if len(ratings) < _MIN_GOALIE_STARTS_LOG:
        return None
    if len(ratings) == 1:
        return 100.0
    mean = sum(ratings) / len(ratings)
    variance = sum((r - mean) ** 2 for r in ratings) / len(ratings)
    stdev = math.sqrt(variance)
    normalized = min(100.0, (stdev / 15.0) * 100.0)
    return max(0.0, 100.0 - normalized)


def _goalie_season_sv_pct(st: PlayerGoalieStat) -> float | None:
    if st.sv_pct is not None:
        return float(st.sv_pct)
    sa = int(st.sa or 0)
    if sa <= 0:
        return None
    return (sa - int(st.ga or 0)) / sa


def _goalie_gp_pct(st: PlayerGoalieStat) -> int | None:
    gp = int(st.gp or 0)
    gs = st.games_started
    if gp <= 0 or gs is None:
        return None
    return max(0, min(100, int(round(100.0 * int(gs) / gp))))


def _goalie_rebound_rating(ratings_row: dict | None) -> float | None:
    if not ratings_row:
        return None
    raw = ratings_row.get("rebound")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _goalie_metric_row(
    session: Session,
    st: PlayerGoalieStat,
    *,
    season_id: int,
    league_sv_pct: float | None,
    gr_median: float | None,
    gr_p75: float | None,
    gr_p25: float | None,
    ratings_row: dict | None,
) -> _GoalieMetricRow:
    game_ratings = _game_ratings_for_goalie_season(session, st.player_id, season_id)
    start_metrics = _start_quality_metrics(
        game_ratings, median=gr_median, p75=gr_p75, p25=gr_p25
    )
    gsaa = _estimated_goalie_gsaa(st, league_sv_pct)
    metrics: dict[str, float | None] = {
        "sv_pct": _goalie_season_sv_pct(st),
        "gsaa": gsaa,
        "gaa": float(st.gaa) if st.gaa is not None else None,
        "game_rating": float(st.game_rating) if st.game_rating is not None else None,
        "minutes": float(st.minutes_played) if st.minutes_played else None,
        "rebound_rating": _goalie_rebound_rating(ratings_row),
        "consistency": _consistency_score(game_ratings),
        **start_metrics,
    }
    return _GoalieMetricRow(player_id=st.player_id, gp=int(st.gp or 0), metrics=metrics)


def _build_goalie_pool(
    session: Session,
    season_id: int,
    *,
    segment: str,
    min_gp: int,
    ratings_by_player: dict[int, dict | None],
) -> list[_GoalieMetricRow]:
    stats = session.scalars(
        select(PlayerGoalieStat)
        .options(joinedload(PlayerGoalieStat.player))
        .where(
            PlayerGoalieStat.season_id == season_id,
            PlayerGoalieStat.stat_segment == segment,
            PlayerGoalieStat.gp >= min_gp,
        )
    ).all()
    league_sv_pct = _league_goalie_sv_pct(list(stats))
    gr_median, gr_p75, gr_p25 = _season_game_gr_thresholds(session, season_id)
    out: list[_GoalieMetricRow] = []
    for st in stats:
        pid = int(st.player_id)
        ratings_row = ratings_by_player.get(pid)
        if ratings_row is None and st.player:
            from app.services.player_ratings_csv import get_player_ratings_row

            ratings_row = get_player_ratings_row(st.player.fhm_player_id)
            ratings_by_player[pid] = ratings_row
        out.append(
            _goalie_metric_row(
                session,
                st,
                season_id=season_id,
                league_sv_pct=league_sv_pct,
                gr_median=gr_median,
                gr_p75=gr_p75,
                gr_p25=gr_p25,
                ratings_row=ratings_row,
            )
        )
    return out


def _goalie_metric_pools(rows: list[_GoalieMetricRow]) -> dict[str, list[float]]:
    keys = set(_GOALIE_WAR_WEIGHTS) | {k for _l, k, _h in _GOALIE_GRID_KEYS}
    pools: dict[str, list[float]] = {k: [] for k in keys}
    for row in rows:
        for k in keys:
            v = row.metrics.get(k)
            if v is not None and not math.isnan(v):
                pools[k].append(float(v))
    return pools


def _goalie_cell(
    label: str,
    key: str,
    *,
    player_row: _GoalieMetricRow,
    pools: dict[str, list[float]],
    higher: bool = True,
    force_empty: bool = False,
) -> dict[str, Any]:
    if force_empty:
        return {"label": label, "display": "—", "pct": None, "tier": "empty"}
    pool = pools.get(key) or []
    if len(pool) < _MIN_PERCENTILE_POOL:
        return {"label": label, "display": "—", "pct": None, "tier": "empty"}
    pct = percentile_int(player_row.metrics.get(key), pool, higher_is_better=higher)
    return {
        "label": label,
        "display": _display_pct(pct),
        "pct": pct,
        "tier": _pct_tier(pct),
    }


def _load_goalie_ratings_map(
    session: Session, player_ids: list[int], ratings_row: dict | None, player_id: int
) -> dict[int, dict | None]:
    from app.services.player_ratings_csv import get_player_ratings_row

    out: dict[int, dict | None] = {player_id: ratings_row}
    if not player_ids:
        return out
    players = session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    for pl in players:
        pid = int(pl.id)
        if pid not in out:
            out[pid] = get_player_ratings_row(pl.fhm_player_id)
    return out


def _resolve_skater_stat(
    session: Session,
    player_id: int,
    *,
    segment: str,
    prefer_season_id: int | None,
) -> tuple[PlayerSkaterStat | None, Season | None]:
    base = (
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
        .join(Season, Season.id == PlayerSkaterStat.season_id)
        .where(
            PlayerSkaterStat.player_id == player_id,
            PlayerSkaterStat.stat_segment == segment,
        )
    )
    if prefer_season_id is not None:
        st = session.scalars(
            base.where(PlayerSkaterStat.season_id == prefer_season_id).limit(1)
        ).first()
        if st:
            season = session.get(Season, int(prefer_season_id))
            return st, season
    st = session.scalars(base.order_by(Season.start_year.desc().nulls_last()).limit(1)).first()
    if not st:
        return None, None
    season = session.get(Season, int(st.season_id))
    return st, season


def _resolve_goalie_stat(
    session: Session,
    player_id: int,
    *,
    segment: str,
    prefer_season_id: int | None,
) -> tuple[PlayerGoalieStat | None, Season | None]:
    base = (
        select(PlayerGoalieStat)
        .join(Season, Season.id == PlayerGoalieStat.season_id)
        .where(
            PlayerGoalieStat.player_id == player_id,
            PlayerGoalieStat.stat_segment == segment,
        )
    )
    if prefer_season_id is not None:
        st = session.scalars(
            base.where(PlayerGoalieStat.season_id == prefer_season_id).limit(1)
        ).first()
        if st:
            season = session.get(Season, int(prefer_season_id))
            return st, season
    st = session.scalars(base.order_by(Season.start_year.desc().nulls_last()).limit(1)).first()
    if not st:
        return None, None
    season = session.get(Season, int(st.season_id))
    return st, season


def _skater_cell(
    label: str,
    key: str,
    *,
    player_row: _SkaterMetricRow,
    pools: dict[str, list[float]],
    higher: bool = True,
    force_empty: bool = False,
) -> dict[str, Any]:
    if force_empty:
        return {"label": label, "display": "—", "pct": None, "tier": "empty"}
    pool = pools.get(key) or []
    if len(pool) < _MIN_PERCENTILE_POOL:
        return {"label": label, "display": "—", "pct": None, "tier": "empty"}
    pct = percentile_int(player_row.metrics.get(key), pool, higher_is_better=higher)
    return {
        "label": label,
        "display": _display_pct(pct),
        "pct": pct,
        "tier": _pct_tier(pct),
    }


def build_player_analytics_card(
    session: Session,
    player: Player,
    season: Season | None,
    *,
    is_goalie: bool,
    ratings_row: dict | None,
    contract: Any | None,
    player_age: int | None,
    role_title: str | None,
    team: Team | None,
    years_left: int | None,
    photo_url: str | None,
    team_logo_url: str | None,
    raw_dir: Path,
    retired: bool = False,
    segment: str = "rs",
) -> dict[str, Any]:
    prefer_season_id = int(season.id) if season else None
    if is_goalie:
        return _build_goalie_analytics_card(
            session,
            player,
            contract=contract,
            player_age=player_age,
            role_title=role_title,
            team=team,
            years_left=years_left,
            photo_url=photo_url,
            team_logo_url=team_logo_url,
            segment=segment,
            prefer_season_id=prefer_season_id,
            retired=retired,
            ratings_row=ratings_row,
        )

    st, stat_season = _resolve_skater_stat(
        session,
        player.id,
        segment=segment,
        prefer_season_id=prefer_season_id,
    )
    card_team = (st.team if st and st.team else None) or team
    from app.logo_urls import team_logo_url_for_team

    resolved_logo = team_logo_url or (team_logo_url_for_team(card_team) if card_team else None)
    headline = _headline_dict(
        player,
        photo_url=photo_url,
        team_logo_url=resolved_logo,
        team=card_team,
        proj_war_pct=None,
        player_age=player_age,
        role_title=role_title,
        contract=contract,
        years_left=years_left,
        is_goalie=False,
    )
    headline["season_year"] = _season_year_label(stat_season)

    if not st or not stat_season:
        return {
            "enabled": True,
            "is_goalie": False,
            "has_stats": False,
            "headline": headline,
            "grid": _empty_skater_grid(),
            "charts": {"war": {"has_data": False}, "components": {"has_data": False}},
            "footnote": "No season stats on file for percentile rankings.",
        }

    pos_group = _position_group(player.position)
    season_id = int(stat_season.id)
    pool_rows = _build_skater_pool(
        session, season_id, segment=segment, position_group=pos_group, raw_dir=raw_dir
    )
    pools = _metric_pools(pool_rows)
    player_row = _skater_metric_row(session, st, season_id=season_id, segment=segment, raw_dir=raw_dir)

    war_pct = None
    if len(pool_rows) >= _MIN_PERCENTILE_POOL:
        war_pct = _war_pct_from_metrics(player_row.metrics, pools)

    abi = None
    pot = None
    if ratings_row:
        from app.services.player_ratings_csv import fhm_abi_pot_float

        abi = fhm_abi_pot_float(ratings_row.get("ability"))
        pot = fhm_abi_pot_float(ratings_row.get("potential"))
    proj_war_pct = war_pct if retired else _projected_war_pct(
        war_pct,
        age=player_age,
        abi=abi,
        pot=pot,
        game_rating=player_row.metrics.get("game_rating"),
    )
    headline["proj_war_pct"] = proj_war_pct

    pk_empty = not st.shto_seconds or int(st.shto_seconds or 0) < 60
    finishing_empty = not st.shots or int(st.shots or 0) < _MIN_FINISHING_SHOTS
    grid = []
    for label, key, higher in _SKATER_GRID_KEYS:
        force_empty = (key == "sh_pts_per_60" and pk_empty) or (
            key == "finishing" and finishing_empty
        )
        grid.append(
            _skater_cell(
                label,
                key,
                player_row=player_row,
                pools=pools,
                higher=higher,
                force_empty=force_empty,
            )
        )

    season_ids = session.scalars(
        select(PlayerSkaterStat.season_id)
        .where(
            PlayerSkaterStat.player_id == player.id,
            PlayerSkaterStat.stat_segment == segment,
        )
        .distinct()
        .order_by(PlayerSkaterStat.season_id.asc())
    ).all()
    trend_seasons = (
        session.scalars(
            select(Season).where(Season.id.in_(season_ids)).order_by(Season.start_year.asc())
        ).all()
        if season_ids
        else []
    )

    war_labels: list[str] = []
    war_series: list[int | None] = []
    off_series: list[int | None] = []
    def_series: list[int | None] = []
    fin_series: list[int | None] = []

    from app.services.analytics_snapshots import (
        load_player_analytics_snapshots,
        player_snapshot_trend_series,
    )

    snaps = load_player_analytics_snapshots(
        session, int(player.id), segment=segment, is_goalie=False
    )
    if snaps:
        trend = player_snapshot_trend_series(snaps, is_goalie=False)
        war_labels = list(trend["labels"])
        war_series = list(trend["war"])
        off_series = list(trend["off"])
        def_series = list(trend["def"])
        fin_series = list(trend["fin"])
    else:
        for tr_season in trend_seasons:
            tr_pool = _build_skater_pool(
                session, int(tr_season.id), segment=segment, position_group=pos_group, raw_dir=raw_dir
            )
            if len(tr_pool) < _MIN_PERCENTILE_POOL:
                continue
            tr_pools = _metric_pools(tr_pool)
            tr_st = session.scalars(
                select(PlayerSkaterStat)
                .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
                .where(
                    PlayerSkaterStat.player_id == player.id,
                    PlayerSkaterStat.season_id == tr_season.id,
                    PlayerSkaterStat.stat_segment == segment,
                ).limit(1)
            ).first()
            if not tr_st:
                continue
            tr_row = _skater_metric_row(
                session, tr_st, season_id=int(tr_season.id), segment=segment, raw_dir=raw_dir
            )
            war_labels.append(_season_short_label(tr_season))
            war_series.append(_war_pct_from_metrics(tr_row.metrics, tr_pools))
            off_series.append(
                percentile_int(tr_row.metrics.get("game_rating_off"), tr_pools.get("game_rating_off") or [])
            )
            def_series.append(
                percentile_int(tr_row.metrics.get("game_rating_def"), tr_pools.get("game_rating_def") or [])
            )
            fin_series.append(
                percentile_int(tr_row.metrics.get("finishing"), tr_pools.get("finishing") or [])
            )
        if not war_labels and war_pct is not None:
            war_labels = [_season_short_label(stat_season)]
            war_series = [war_pct]
            off_series = [
                percentile_int(player_row.metrics.get("game_rating_off"), pools.get("game_rating_off") or [])
            ]
            def_series = [
                percentile_int(player_row.metrics.get("game_rating_def"), pools.get("game_rating_def") or [])
            ]
            fin_series = [
                percentile_int(player_row.metrics.get("finishing"), pools.get("finishing") or [])
            ]

    war_chart = chart_svg(
        war_labels,
        [{"values": war_series, "class": "player-analytics-card__chart-line--war"}],
    )
    comp_chart = chart_svg(
        war_labels,
        [
            {"values": off_series, "class": "player-analytics-card__chart-line--off"},
            {"values": def_series, "class": "player-analytics-card__chart-line--def"},
            {"values": fin_series, "class": "player-analytics-card__chart-line--fin", "stroke_dasharray": "4 3"},
        ],
    )

    group_label = "forwards" if pos_group == "forward" else "defensemen"
    season_label = _season_short_label(stat_season)
    retired_note = " Retired player." if retired else ""
    charts = {
        "war": war_chart,
        "components": comp_chart,
    }
    return {
        "enabled": True,
        "is_goalie": False,
        "has_stats": True,
        "headline": headline,
        "grid": grid,
        "charts": charts,
        "footnote": (
            f"League percentile vs qualified {group_label} in the {season_label} {segment.upper()} season "
            f"(not team share). Competition uses opponent game rating; teammates use linemate game rating."
            f"{retired_note}"
        ),
    }


def _build_goalie_analytics_card(
    session: Session,
    player: Player,
    *,
    contract: Any | None,
    player_age: int | None,
    role_title: str | None,
    team: Team | None,
    years_left: int | None,
    photo_url: str | None,
    team_logo_url: str | None,
    segment: str,
    prefer_season_id: int | None,
    retired: bool,
    ratings_row: dict | None,
) -> dict[str, Any]:
    st, stat_season = _resolve_goalie_stat(
        session,
        player.id,
        segment=segment,
        prefer_season_id=prefer_season_id,
    )
    from app.logo_urls import team_logo_url_for_team

    card_team = (st.team if st and st.team else None) or team
    resolved_logo = team_logo_url or (team_logo_url_for_team(card_team) if card_team else None)
    headline = _headline_dict(
        player,
        photo_url=photo_url,
        team_logo_url=resolved_logo,
        team=card_team,
        proj_war_pct=None,
        player_age=player_age,
        role_title=role_title,
        contract=contract,
        years_left=years_left,
        is_goalie=True,
    )
    headline["season_year"] = _season_year_label(stat_season)

    if not st or not stat_season:
        headline["gp_pct"] = None
        return {
            "enabled": True,
            "is_goalie": True,
            "has_stats": False,
            "headline": headline,
            "grid": _empty_goalie_grid(),
            "charts": {"war": {"has_data": False}, "components": {"has_data": False}},
            "footnote": "No season stats on file for percentile rankings.",
        }

    season_id = int(stat_season.id)
    min_gp = _adaptive_min_gp(session, PlayerGoalieStat, season_id, segment, MIN_GOALIE_GP)
    ratings_by_player = _load_goalie_ratings_map(
        session, [], ratings_row, int(player.id)
    )
    pool_rows = _build_goalie_pool(
        session,
        season_id,
        segment=segment,
        min_gp=min_gp,
        ratings_by_player=ratings_by_player,
    )
    pools = _goalie_metric_pools(pool_rows)
    league_sv_pct = _league_goalie_sv_pct(
        session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == season_id,
                PlayerGoalieStat.stat_segment == segment,
                PlayerGoalieStat.gp >= min_gp,
            )
        ).all()
    )
    gr_median, gr_p75, gr_p25 = _season_game_gr_thresholds(session, season_id)
    player_row = _goalie_metric_row(
        session,
        st,
        season_id=season_id,
        league_sv_pct=league_sv_pct,
        gr_median=gr_median,
        gr_p75=gr_p75,
        gr_p25=gr_p25,
        ratings_row=ratings_by_player.get(int(player.id)),
    )

    war_pct = None
    if len(pool_rows) >= _MIN_PERCENTILE_POOL:
        war_pct = _goalie_war_pct_from_metrics(player_row.metrics, pools)
    headline["proj_war_pct"] = war_pct
    headline["gp_pct"] = _goalie_gp_pct(st)

    game_ratings = _game_ratings_for_goalie_season(session, player.id, season_id)
    start_empty = len(game_ratings) < _MIN_GOALIE_STARTS_LOG
    rebound_empty = player_row.metrics.get("rebound_rating") is None

    grid = [
        _goalie_cell(label, key, player_row=player_row, pools=pools, higher=higher)
        if key not in ("quality_start_pct", "excellent_start_pct", "bad_start_pct", "rebound_rating")
        else _goalie_cell(
            label,
            key,
            player_row=player_row,
            pools=pools,
            higher=higher,
            force_empty=(start_empty if key != "rebound_rating" else rebound_empty),
        )
        for label, key, higher in _GOALIE_GRID_KEYS
    ]

    season_ids = session.scalars(
        select(PlayerGoalieStat.season_id)
        .where(
            PlayerGoalieStat.player_id == player.id,
            PlayerGoalieStat.stat_segment == segment,
        )
        .distinct()
        .order_by(PlayerGoalieStat.season_id.asc())
    ).all()
    trend_seasons = (
        session.scalars(
            select(Season).where(Season.id.in_(season_ids)).order_by(Season.start_year.asc())
        ).all()
        if season_ids
        else []
    )

    war_labels: list[str] = []
    war_series: list[int | None] = []
    sv_series: list[float | None] = []
    league_sv_series: list[float | None] = []

    from app.services.analytics_snapshots import (
        load_player_analytics_snapshots,
        player_snapshot_trend_series,
    )

    snaps = load_player_analytics_snapshots(
        session, int(player.id), segment=segment, is_goalie=True
    )
    if snaps:
        trend = player_snapshot_trend_series(snaps, is_goalie=True)
        war_labels = list(trend["labels"])
        war_series = list(trend["war"])
        sv_series = list(trend["sv"])
        league_sv_series = list(trend["league_sv"])
    else:
        for tr_season in trend_seasons:
            tr_min_gp = _adaptive_min_gp(
                session, PlayerGoalieStat, int(tr_season.id), segment, MIN_GOALIE_GP
            )
            tr_pool = _build_goalie_pool(
                session,
                int(tr_season.id),
                segment=segment,
                min_gp=tr_min_gp,
                ratings_by_player=dict(ratings_by_player),
            )
            if len(tr_pool) < _MIN_PERCENTILE_POOL:
                continue
            tr_pools = _goalie_metric_pools(tr_pool)
            tr_st = session.scalars(
                select(PlayerGoalieStat).where(
                    PlayerGoalieStat.player_id == player.id,
                    PlayerGoalieStat.season_id == tr_season.id,
                    PlayerGoalieStat.stat_segment == segment,
                ).limit(1)
            ).first()
            if not tr_st:
                continue
            tr_league_sv = _league_goalie_sv_pct(
                session.scalars(
                    select(PlayerGoalieStat).where(
                        PlayerGoalieStat.season_id == tr_season.id,
                        PlayerGoalieStat.stat_segment == segment,
                        PlayerGoalieStat.gp >= tr_min_gp,
                    )
                ).all()
            )
            tr_gr_median, tr_gr_p75, tr_gr_p25 = _season_game_gr_thresholds(session, int(tr_season.id))
            tr_row = _goalie_metric_row(
                session,
                tr_st,
                season_id=int(tr_season.id),
                league_sv_pct=tr_league_sv,
                gr_median=tr_gr_median,
                gr_p75=tr_gr_p75,
                gr_p25=tr_gr_p25,
                ratings_row=ratings_by_player.get(int(player.id)),
            )
            war_labels.append(_season_short_label(tr_season))
            war_series.append(_goalie_war_pct_from_metrics(tr_row.metrics, tr_pools))
            sv = _goalie_season_sv_pct(tr_st)
            sv_series.append(round(sv * 100.0, 1) if sv is not None else None)
            league_sv_series.append(
                round(tr_league_sv * 100.0, 1) if tr_league_sv is not None else None
            )
        if not war_labels and war_pct is not None:
            war_labels = [_season_short_label(stat_season)]
            war_series = [war_pct]
            sv = _goalie_season_sv_pct(st)
            sv_series = [round(sv * 100.0, 1) if sv is not None else None]
            league_sv_series = [round(league_sv_pct * 100.0, 1) if league_sv_pct is not None else None]

    war_chart = chart_svg(
        war_labels,
        [{"values": war_series, "class": "player-analytics-card__chart-line--war"}],
    )
    sv_values = [v for v in sv_series if v is not None]
    league_sv_values = [v for v in league_sv_series if v is not None]
    sv_ymin = min(sv_values + league_sv_values) - 1.0 if sv_values or league_sv_values else 85.0
    sv_ymax = max(sv_values + league_sv_values) + 1.0 if sv_values or league_sv_values else 95.0
    sv_chart = chart_svg(
        war_labels,
        [
            {"values": sv_series, "class": "player-analytics-card__chart-line--war"},
            {
                "values": league_sv_series,
                "class": "player-analytics-card__chart-line--fin",
                "stroke_dasharray": "4 3",
            },
        ],
        ymin=sv_ymin,
        ymax=sv_ymax,
    )

    season_label = _season_short_label(stat_season)
    retired_note = " Retired player." if retired else ""
    return {
        "enabled": True,
        "is_goalie": True,
        "has_stats": True,
        "headline": headline,
        "grid": grid,
        "charts": {"war": war_chart, "components": sv_chart},
        "footnote": (
            f"League percentile vs qualified goalies in the {season_label} {segment.upper()} season "
            f"(not team share). GSAA estimated when missing; SV% chart compares to league average."
            f"{retired_note}"
        ),
    }
