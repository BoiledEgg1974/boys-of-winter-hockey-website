"""Site-computed FHM-style line roles (1–100) from player_ratings.csv attributes.

These are not official FHM role exports. Each role is a weighted mix of 0–20
attribute columns, scaled the same way as staff role overalls.
"""
from __future__ import annotations

from typing import Any

from app.services.player_overall_score import _parse_rating_cell

RoleSpec = tuple[str, str, str, tuple[tuple[str, float], ...]]

# (key, label, group, weighted attribute keys)
FORWARD_ROLES: tuple[RoleSpec, ...] = (
    ("sniper", "Sniper", "forwards", (
        ("shooting_accuracy", 1.2), ("shooting_range", 1.0), ("getting_open", 1.0),
        ("offensive_read", 0.8), ("shooting", 0.8),
    )),
    ("playmaker", "Playmaker", "forwards", (
        ("passing", 1.2), ("puck_handling", 1.0), ("offensive_read", 1.0),
        ("playmaking", 1.0), ("hockey_sense", 0.7),
    )),
    ("power_forward", "Power Forward", "forwards", (
        ("strength", 1.1), ("hitting", 1.0), ("screening", 0.9),
        ("shooting", 0.8), ("physicality", 0.8),
    )),
    ("grinder", "Grinder", "forwards", (
        ("checking", 1.1), ("hitting", 1.0), ("determination", 0.9),
        ("stamina", 0.8), ("defensive_read", 0.8),
    )),
    ("two_way_forward", "Two Way Forward", "forwards", (
        ("defending", 1.0), ("offensive_read", 1.0), ("defensive_read", 1.0),
        ("positioning", 0.9), ("hockey_sense", 0.8),
    )),
    ("dangler", "Dangler", "forwards", (
        ("puck_handling", 1.2), ("agility", 1.0), ("acceleration", 0.9),
        ("getting_open", 0.8), ("playmaking", 0.7),
    )),
    ("perimeter_shooter", "Perimeter Shooter", "forwards", (
        ("shooting_accuracy", 1.1), ("shooting_range", 1.2), ("getting_open", 0.9),
        ("speed", 0.7),
    )),
    ("screener", "Screener", "forwards", (
        ("screening", 1.3), ("strength", 1.0), ("positioning", 0.8),
        ("getting_open", 0.7),
    )),
    ("speedy_forward", "Speedy Forward", "forwards", (
        ("speed", 1.3), ("acceleration", 1.1), ("agility", 0.9), ("stamina", 0.7),
    )),
    ("backchecking_forward", "Backchecking Forward", "forwards", (
        ("defensive_read", 1.1), ("checking", 1.0), ("positioning", 1.0),
        ("speed", 0.8), ("stamina", 0.8),
    )),
    ("setup_man", "Setup Man", "forwards", (
        ("passing", 1.3), ("offensive_read", 1.0), ("hockey_sense", 0.9),
        ("playmaking", 0.9),
    )),
    ("garbage_collector", "Garbage Collector", "forwards", (
        ("screening", 1.1), ("getting_open", 1.1), ("shooting_accuracy", 1.0),
        ("positioning", 0.8),
    )),
    ("aggressive_forechecker", "Aggressive Forechecker", "forwards", (
        ("checking", 1.1), ("hitting", 1.0), ("acceleration", 1.0),
        ("aggression", 0.9), ("speed", 0.8),
    )),
    ("shadow", "Shadow", "forwards", (
        ("defensive_read", 1.2), ("stickchecking", 1.1), ("positioning", 1.0),
        ("checking", 0.8),
    )),
    ("gretzkys_office", "Gretzky's Office", "forwards", (
        ("puck_handling", 1.1), ("passing", 1.0), ("getting_open", 1.1),
        ("offensive_read", 1.0), ("hockey_sense", 0.8),
    )),
    ("up_and_down_winger", "Up and Down Winger", "forwards", (
        ("stamina", 1.2), ("speed", 1.0), ("checking", 0.9), ("positioning", 0.8),
    )),
    ("counterattacking", "Counterattacking", "forwards", (
        ("speed", 1.1), ("acceleration", 1.1), ("passing", 0.9),
        ("offensive_read", 0.9),
    )),
    ("agitator", "Agitator", "forwards", (
        ("aggression", 1.2), ("hitting", 1.1), ("fighting", 1.0), ("bravery", 0.8),
    )),
    ("goon", "Goon", "forwards", (
        ("fighting", 1.4), ("aggression", 1.1), ("strength", 0.9), ("hitting", 0.8),
    )),
    ("punishing_forward", "Punishing Forward", "forwards", (
        ("hitting", 1.3), ("strength", 1.1), ("checking", 0.9), ("physicality", 0.8),
    )),
)

DEFENSE_ROLES: tuple[RoleSpec, ...] = (
    ("offensive_d", "Offensive D", "defense", (
        ("offensive_read", 1.1), ("passing", 1.1), ("puck_handling", 1.0),
        ("shooting", 0.8),
    )),
    ("stay_at_home", "Stay-at-Home", "defense", (
        ("positioning", 1.2), ("defensive_read", 1.2), ("shot_blocking", 1.0),
        ("checking", 0.8),
    )),
    ("two_way_d", "Two-Way D", "defense", (
        ("defending", 1.1), ("offensive_read", 0.9), ("defensive_read", 1.0),
        ("hockey_sense", 0.9),
    )),
    ("puck_mover", "Puck Mover", "defense", (
        ("passing", 1.2), ("puck_handling", 1.1), ("speed", 0.9),
        ("hockey_sense", 0.8),
    )),
    ("enforcer_d", "Enforcer D", "defense", (
        ("fighting", 1.2), ("hitting", 1.1), ("strength", 1.0), ("aggression", 0.8),
    )),
    ("shutdown", "Shutdown", "defense", (
        ("stickchecking", 1.2), ("checking", 1.1), ("positioning", 1.0),
        ("defensive_read", 1.0),
    )),
)

GOALIE_ROLES: tuple[RoleSpec, ...] = (
    ("starting_goalie", "Starting Goalie", "goalies", (
        ("reflexes", 1.1), ("g_positioning", 1.1), ("glove", 0.9),
        ("blocker", 0.8), ("rebound", 0.8),
    )),
    ("positional_goalie", "Positional Goalie", "goalies", (
        ("g_positioning", 1.3), ("rebound", 1.0), ("recovery", 0.9),
        ("low_shots", 0.8),
    )),
    ("athletic_goalie", "Athletic Goalie", "goalies", (
        ("reflexes", 1.3), ("agility", 1.0), ("recovery", 1.0), ("g_skating", 0.7),
    )),
)

ALL_ROLES: tuple[RoleSpec, ...] = FORWARD_ROLES + DEFENSE_ROLES + GOALIE_ROLES
ROLES_BY_KEY: dict[str, RoleSpec] = {spec[0]: spec for spec in ALL_ROLES}

COMPLEMENTARY_ROLES: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"sniper", "playmaker"}),
        frozenset({"sniper", "setup_man"}),
        frozenset({"sniper", "gretzkys_office"}),
        frozenset({"playmaker", "garbage_collector"}),
        frozenset({"playmaker", "screener"}),
        frozenset({"power_forward", "playmaker"}),
        frozenset({"offensive_d", "stay_at_home"}),
        frozenset({"puck_mover", "shutdown"}),
        frozenset({"two_way_d", "offensive_d"}),
    }
)


def _attr(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    return _parse_rating_cell(row.get(key))


def score_role(row: dict[str, Any] | None, weights: tuple[tuple[str, float], ...]) -> int | None:
    """Weighted 0–20 attributes → 1–100."""
    total = 0.0
    weight_sum = 0.0
    for key, w in weights:
        val = _attr(row, key)
        if val is None:
            continue
        total += max(0.0, min(20.0, float(val))) * float(w)
        weight_sum += float(w)
    if weight_sum <= 0:
        return None
    avg = total / weight_sum
    return int(max(1, min(100, round(1.0 + 99.0 * (avg / 20.0)))))


def roles_for_group(group: str) -> tuple[RoleSpec, ...]:
    g = (group or "").strip().lower()
    if g == "defense":
        return DEFENSE_ROLES
    if g == "goalies":
        return GOALIE_ROLES
    return FORWARD_ROLES


def player_role_group(position: str | None, ratings_row: dict[str, Any] | None = None) -> str:
    pos = (position or "").strip().upper()
    if pos == "G" or pos.startswith("G"):
        return "goalies"
    if pos in {"D", "LD", "RD"}:
        return "defense"
    if pos in {"LW", "C", "RW"}:
        return "forwards"
    g = _attr(ratings_row, "g") or -1.0
    d = max(_attr(ratings_row, "ld") or -1.0, _attr(ratings_row, "rd") or -1.0)
    f = max(
        _attr(ratings_row, "lw") or -1.0,
        _attr(ratings_row, "c") or -1.0,
        _attr(ratings_row, "rw") or -1.0,
    )
    best = max((g, "goalies"), (d, "defense"), (f, "forwards"), key=lambda x: x[0])
    return best[1] if best[0] >= 0 else "forwards"


def role_scores_for_player(
    ratings_row: dict[str, Any] | None,
    *,
    position: str | None = None,
) -> list[dict[str, Any]]:
    group = player_role_group(position, ratings_row)
    scored: list[dict[str, Any]] = []
    for key, label, grp, weights in roles_for_group(group):
        rating = score_role(ratings_row, weights)
        if rating is None:
            continue
        scored.append({"key": key, "label": label, "group": grp, "rating": rating})
    scored.sort(key=lambda r: (-int(r["rating"]), str(r["label"])))
    return scored


def default_role_key(scores: list[dict[str, Any]]) -> str | None:
    return str(scores[0]["key"]) if scores else None


def role_rating_for_key(scores: list[dict[str, Any]], key: str | None) -> int | None:
    if not key:
        return None
    for row in scores:
        if row["key"] == key:
            return int(row["rating"])
    return None


def line_ability(ratings: list[int | None]) -> float | None:
    vals = [int(v) for v in ratings if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def line_ability_grade(ability: float | None, *, kind: str | None = None) -> dict[str, Any] | None:
    """Map mean role rating to a 1st–Depth equivalent (no bar)."""
    if ability is None:
        return None
    pair = (kind or "").strip().lower() == "defense"
    score = float(ability)
    if score >= 85:
        key, label = "1st", "1st Pair" if pair else "1st line"
    elif score >= 76:
        key, label = "2nd", "2nd Pair" if pair else "2nd line"
    elif score >= 68:
        key, label = "3rd", "3rd Pair" if pair else "3rd line"
    elif score >= 60:
        key, label = "4th", "4th Pair" if pair else "4th line"
    else:
        key, label = "depth", "Depth pair" if pair else "Depth"
    return {"key": key, "label": label, "score": score}


def line_chemistry(
    role_keys: list[str | None],
    hands: list[str | None],
) -> int | None:
    """Simple complementarity + handedness mix. 1–100."""
    keys = [k for k in role_keys if k]
    if not keys:
        return None
    score = 55.0
    uniq = set(keys)
    if len(keys) != len(uniq):
        score -= 8.0 * (len(keys) - len(uniq))
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if frozenset({a, b}) in COMPLEMENTARY_ROLES:
                score += 8.0
    norms = []
    for h in hands:
        t = (h or "").strip().lower()
        if t.startswith("l"):
            norms.append("l")
        elif t.startswith("r"):
            norms.append("r")
    if "l" in norms and "r" in norms:
        score += 6.0
    return int(max(1, min(100, round(score))))
