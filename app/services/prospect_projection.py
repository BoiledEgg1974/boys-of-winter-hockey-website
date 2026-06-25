"""BOWL-native prospect projection metrics (Star%, BOWL%, BOWLe) from ratings exports."""
from __future__ import annotations

import math
from typing import Any

from app.models import Player, Season
from app.services.draft_hub_eligibility import (
    DraftEligibilityParams,
    default_eligibility_for_league,
    draft_eligible_timeline_year_for_league,
)
from app.services.player_overall_score import (
    GOALIE_OVERVIEW_ATTR_KEYS,
    SKATER_OVERVIEW_ATTR_KEYS,
    compute_player_overall_100,
    player_is_goalie_for_overall,
)
from app.services.player_percentiles import chart_svg

PROSPECT_OVERVIEW_HEADERS: tuple[tuple[str, str, str], ...] = (
    ("Skating", "SKT", "skating"),
    ("Shooting", "SHT", "shooting"),
    ("Playmaking", "PLM", "playmaking"),
    ("Defending", "DEF", "defending"),
    ("Physicality", "PHY", "physicality"),
    ("Conditioning", "CON", "conditioning"),
    ("Character", "CHR", "character"),
    ("Hockey sense", "HSN", "hockey_sense"),
)

PROSPECT_PROJECTION_SORT_KEYS: frozenset[str] = frozenset(
    {"star", "bowl", "bowle_dy_m1", "bowle_dy"}
)

PROSPECT_PROJECTION_HEADERS: tuple[tuple[str, str, str], ...] = (
    (
        "star",
        "BOWL Star",
        "BOWL star projection — chance of becoming a BOWL star (top 20% WAR for forwards, top 15% for defense)",
    ),
    (
        "bowl",
        "BOWL Lg%",
        "BOWL league projection — chance of a 200+ game BOWL career",
    ),
    (
        "bowle_dy_m1",
        "DY-1e",
        "BOWLe (BOWL Equivalency) at draft year minus one — projected BOWL impact on a 0–30 scale "
        "(NHLe-style model from ABI, POT, OVR, and overview ratings). Rough benchmarks: 8 = depth/fringe "
        "roster, 15 = everyday NHLer, 22 = strong top-six or top-pair, 28+ = elite. One year younger than "
        "draft eligibility; compare with DYe to see projected growth.",
    ),
    (
        "bowle_dy",
        "DYe",
        "BOWLe at draft eligibility age — same 0–30 BOWL impact scale, modeled when the prospect reaches "
        "draft year with a small potential upside bump. Usually higher than DY-1e; the gap reflects expected "
        "development. Benchmarks: ~15 average NHLer, ~22 strong starter, ~28+ elite/franchise tier.",
    ),
)

BOWLE_SCALE_GUIDE = (
    "0–30 BOWL impact scale (NHLe-style): ~8 depth, ~15 everyday, ~22 strong starter, ~28+ elite"
)

PROSPECT_PROJECTION_FOOTNOTE = (
    "BOWL Star, BOWL Lg%, and BOWLe are BOWL-native proxies from ABI, POT, OVR, age, and overview "
    "attributes — inspired by HockeyStats NHLe methodology (hockeystats.com/methodology/nhle), "
    "not trained on historical draft outcomes. BOWL Star = top-tier WAR projection; BOWL Lg% = 200+ "
    f"career games in BOWL. BOWLe (DY-1e / DYe) = {BOWLE_SCALE_GUIDE.lower()}."
)


def format_projection_pct(pct: int | None) -> str:
    if pct is None:
        return "—"
    if pct >= 99:
        return ">99%"
    return f"{pct}%"


def _norm_abi_pot(v: float) -> float:
    return max(0.0, min(1.0, (v - 0.5) / 4.5))


def _parse_float(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _mean_attr_norm(ratings_row: dict[str, Any] | None, *, is_goalie: bool) -> float | None:
    if not ratings_row:
        return None
    keys = GOALIE_OVERVIEW_ATTR_KEYS if is_goalie else SKATER_OVERVIEW_ATTR_KEYS
    vals: list[float] = []
    for key in keys:
        v = _parse_float(ratings_row.get(key))
        if v is not None:
            vals.append(max(0.0, min(1.0, v / 20.0)))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _talent_index(
    abi: float | None,
    pot: float | None,
    ovr: int | None,
    ratings_row: dict[str, Any] | None,
    *,
    is_goalie: bool,
) -> float | None:
    if abi is None and pot is None:
        return None
    parts: list[tuple[float, float]] = []
    if abi is not None:
        parts.append((_norm_abi_pot(abi), 0.22))
    if pot is not None:
        parts.append((_norm_abi_pot(pot), 0.36))
    if ovr is not None:
        parts.append((max(0.0, min(1.0, ovr / 100.0)), 0.22))
    attr_mean = _mean_attr_norm(ratings_row, is_goalie=is_goalie)
    if attr_mean is not None:
        parts.append((attr_mean, 0.20))
    if not parts:
        return None
    weight_sum = sum(w for _v, w in parts)
    return sum(v * w for v, w in parts) / weight_sum


def _draft_year_age(
    current_age: float | None,
    *,
    league_slug: str,
    params: DraftEligibilityParams | None,
) -> int:
    defaults = params or default_eligibility_for_league(league_slug)
    if current_age is None:
        return int(defaults.min_age_years)
    age_i = int(math.floor(current_age))
    return max(
        int(defaults.min_age_years),
        min(int(defaults.max_age_years), age_i),
    )


def _age_ramp(age: int, dy_age: int) -> float:
    if dy_age <= 15:
        return 1.0
    return max(0.45, min(1.0, 0.45 + 0.55 * (age - 15) / (dy_age - 15)))


def _bowle_value(
    talent: float,
    age: int,
    dy_age: int,
    pot_norm: float,
    *,
    at_draft_year: bool,
) -> float:
    ramp = _age_ramp(age, dy_age)
    val = talent * 30.0 * ramp
    if at_draft_year:
        val *= 1.0 + 0.10 * pot_norm
    return round(max(0.0, min(30.0, val)), 1)


def _position_star_adjustment(player: Player) -> float:
    pos = (player.position or "").strip().upper()
    if pos == "D":
        return -0.18
    if pos == "G":
        return -0.22
    return 0.0


def _logistic_pct(score: float, *, steepness: float = 4.2) -> int:
    prob = 1.0 / (1.0 + math.exp(-score * steepness))
    return max(1, min(99, int(round(prob * 100))))


def _mini_bowle_chart(dy_m1: float | None, dy: float | None) -> dict[str, Any]:
    if dy_m1 is None or dy is None:
        return {"has_data": False}
    ymax = max(30.0, dy_m1, dy) + 2.0
    chart = chart_svg(
        ["DY-1", "DY"],
        [{"values": [dy_m1, dy], "class": "prospect-proj-popover__chart-line"}],
        width=200,
        height=72,
        ymin=0.0,
        ymax=ymax,
    )
    if not chart.get("has_data"):
        return {"has_data": False}
    ticks = [0, ymax * 0.25, ymax * 0.5, ymax * 0.75, ymax]
    pad_l, pad_r, pad_t, pad_b = 30, 8, 8, 22
    inner_h = chart["height"] - pad_t - pad_b

    def y_at(v: float) -> float:
        return pad_t + inner_h - (v / ymax) * inner_h

    chart["y_labels"] = [
        {"x": 2, "y": y_at(t) + 3, "text": str(int(round(t)))} for t in ticks
    ]
    chart["grid_lines"] = [
        {
            "x1": pad_l,
            "x2": chart["width"] - pad_r,
            "y": y_at(t),
        }
        for t in ticks
    ]
    return chart


def _format_height(height_inches: int | None) -> str | None:
    if height_inches is None or height_inches <= 0:
        return None
    ft, inches = divmod(int(height_inches), 12)
    return f"{ft}'{inches}\""


def build_prospect_projection(
    player: Player,
    *,
    abi: float | None,
    pot: float | None,
    ratings_row: dict[str, Any] | None,
    age: float | None,
    league_slug: str,
    season: Season | None,
    eligibility_params: DraftEligibilityParams | None = None,
) -> dict[str, Any]:
    is_goalie = player_is_goalie_for_overall(player)
    ovr = compute_player_overall_100(abi, pot, ratings_row, is_goalie=is_goalie)
    talent = _talent_index(abi, pot, ovr, ratings_row, is_goalie=is_goalie)
    empty = {
        "star_pct": None,
        "bowl_pct": None,
        "bowle_dy_m1": None,
        "bowle_dy": None,
        "star_display": "—",
        "bowl_display": "—",
        "bowle_dy_m1_display": "—",
        "bowle_dy_display": "—",
        "popover": None,
    }
    if talent is None or pot is None:
        return empty

    dy_age = _draft_year_age(age, league_slug=league_slug, params=eligibility_params)
    pot_norm = _norm_abi_pot(pot)
    bowle_dy_m1 = _bowle_value(talent, dy_age - 1, dy_age, pot_norm, at_draft_year=False)
    bowle_dy = _bowle_value(talent, dy_age, dy_age, pot_norm, at_draft_year=True)

    age_f = float(age) if age is not None else float(dy_age)
    age_risk = max(0.0, (age_f - dy_age) * 0.12)
    pos_adj = _position_star_adjustment(player)
    bowle_norm = bowle_dy / 30.0

    star_score = (
        2.4 * (talent - 0.45)
        + 1.1 * (pot_norm - 0.45)
        + 0.9 * bowle_norm
        + pos_adj
        - age_risk
        - 0.35
    )
    bowl_score = (
        1.9 * (talent - 0.38)
        + 0.85 * (pot_norm - 0.38)
        + 0.65 * bowle_norm
        - age_risk * 0.6
        - 0.55
    )
    star_pct = _logistic_pct(star_score, steepness=4.0)
    bowl_pct = _logistic_pct(bowl_score, steepness=3.6)

    timeline_year = draft_eligible_timeline_year_for_league(
        league_slug,
        int(season.start_year) if season and season.start_year else None,
        int(season.end_year) if season and season.end_year else None,
        dy_age,
    )

    popover = {
        "name": player.full_name,
        "nationality": (player.nationality or "").strip() or None,
        "age": round(age_f, 1) if age is not None else None,
        "height": _format_height(player.height_inches),
        "weight": int(player.weight_lbs) if player.weight_lbs else None,
        "position": (player.position or "").strip() or None,
        "star_pct": star_pct,
        "bowl_pct": bowl_pct,
        "bowle_dy_m1": bowle_dy_m1,
        "bowle_dy": bowle_dy,
        "timeline_year": timeline_year,
        "chart": _mini_bowle_chart(bowle_dy_m1, bowle_dy),
    }

    return {
        "star_pct": star_pct,
        "bowl_pct": bowl_pct,
        "bowle_dy_m1": bowle_dy_m1,
        "bowle_dy": bowle_dy,
        "star_display": format_projection_pct(star_pct),
        "bowl_display": format_projection_pct(bowl_pct),
        "bowle_dy_m1_display": f"{bowle_dy_m1:.1f}",
        "bowle_dy_display": f"{bowle_dy:.1f}",
        "popover": popover,
    }


def projection_sort_value(projection: dict[str, Any] | None, sort_key: str) -> float | None:
    if not projection:
        return None
    if sort_key == "star":
        return float(projection["star_pct"]) if projection.get("star_pct") is not None else None
    if sort_key == "bowl":
        return float(projection["bowl_pct"]) if projection.get("bowl_pct") is not None else None
    if sort_key == "bowle_dy_m1":
        v = projection.get("bowle_dy_m1")
        return float(v) if v is not None else None
    if sort_key == "bowle_dy":
        v = projection.get("bowle_dy")
        return float(v) if v is not None else None
    return None
