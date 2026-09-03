"""Site-computed FHM-style line roles (1–100) from player_ratings.csv attributes.

These are not official FHM role exports. Each role is a weighted mix of 0–20
attribute columns, scaled the same way as staff role overalls.
"""
from __future__ import annotations

from collections import Counter
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

# Primary style family for line-makeup labels (not 1st/2nd/3rd line tiers).
FORWARD_ROLE_FAMILY: dict[str, str] = {
    "sniper": "scoring",
    "perimeter_shooter": "scoring",
    "garbage_collector": "scoring",
    "playmaker": "playmaking",
    "setup_man": "playmaking",
    "gretzkys_office": "playmaking",
    "dangler": "playmaking",
    "backchecking_forward": "checking",
    "shadow": "checking",
    "grinder": "checking",
    "two_way_forward": "checking",
    "up_and_down_winger": "checking",
    "speedy_forward": "speed",
    "counterattacking": "speed",
    "power_forward": "physical",
    "punishing_forward": "physical",
    "agitator": "physical",
    "goon": "physical",
    "screener": "physical",
    "aggressive_forechecker": "physical",
}

DEFENSE_ROLE_FAMILY: dict[str, str] = {
    "offensive_d": "puck",
    "puck_mover": "puck",
    "stay_at_home": "shutdown",
    "shutdown": "shutdown",
    "two_way_d": "two_way",
    "enforcer_d": "physical",
}

_PUNISHING_ROLES = frozenset({"punishing_forward", "goon", "agitator"})
_SHUTDOWN_ROLES = frozenset({"shadow", "backchecking_forward", "shutdown", "stay_at_home"})
_SHOOTING_ROLES = frozenset({"sniper", "perimeter_shooter"})
_PLAYMAKING_ROLES = frozenset({"playmaker", "setup_man", "gretzkys_office"})
_SPEED_ROLES = frozenset({"speedy_forward", "counterattacking"})
_NETFRONT_ROLES = frozenset({"garbage_collector", "screener", "power_forward"})
_FORECHECK_ROLES = frozenset({"aggressive_forechecker"})
_PHYSICAL_ROLES = frozenset(
    {"punishing_forward", "goon", "agitator", "power_forward", "screener", "aggressive_forechecker", "enforcer_d"}
)

_FAMILY_TITLE: dict[str, str] = {
    "scoring": "Scoring",
    "playmaking": "Playmaking",
    "checking": "Checking",
    "physical": "Physical",
    "speed": "Speed",
    "puck": "Puck-moving",
    "shutdown": "Shutdown",
    "two_way": "Two-way",
}

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


def _unit_noun(kind: str | None) -> str:
    k = (kind or "").strip().lower()
    if k == "defense":
        return "pair"
    if k in {"powerplay", "penalty"}:
        return "unit"
    return "line"


def _role_family(key: str, kind: str | None) -> str | None:
    if key in FORWARD_ROLE_FAMILY:
        return FORWARD_ROLE_FAMILY[key]
    fam = DEFENSE_ROLE_FAMILY.get(key)
    if not fam:
        return None
    if (kind or "").strip().lower() == "defense":
        return fam
    if fam == "puck":
        return "playmaking"
    if fam == "physical":
        return "physical"
    return "checking"


def _count_roles(keys: list[str], group: frozenset[str]) -> int:
    return sum(1 for k in keys if k in group)


def _family_label(fam: str, noun: str, *, kind: str | None) -> str:
    if fam == "playmaking" and (kind or "").strip().lower() == "powerplay":
        title = "Setup"
    elif fam == "scoring" and (kind or "").strip().lower() == "powerplay":
        title = "Shooting"
    else:
        title = _FAMILY_TITLE.get(fam, "Balanced")
    return f"{title} {noun}"


def _combo_label(fams: set[str], noun: str, *, kind: str | None) -> str:
    if "scoring" in fams and "playmaking" in fams:
        return _family_label("scoring" if (kind or "").strip().lower() != "powerplay" else "playmaking", noun, kind=kind)
    if "checking" in fams and ("scoring" in fams or "playmaking" in fams):
        return f"Two-way {noun}"
    if "physical" in fams and "speed" in fams:
        return f"Forechecking {noun}"
    if "physical" in fams and "checking" in fams:
        return f"Physical {noun}"
    if "speed" in fams and "playmaking" in fams:
        return f"Transition {noun}"
    if "speed" in fams and "scoring" in fams:
        return f"Speed {noun}"
    if "physical" in fams and "scoring" in fams:
        return f"Net-front {noun}"
    if "puck" in fams and "shutdown" in fams:
        return f"Two-way {noun}"
    if "puck" in fams and "two_way" in fams:
        return f"Two-way {noun}"
    if "shutdown" in fams and "two_way" in fams:
        return f"Shutdown {noun}"
    if "physical" in fams and "shutdown" in fams:
        return f"Physical {noun}"
    if "physical" in fams and "puck" in fams:
        return f"Two-way {noun}"
    if len(fams) == 1:
        return _family_label(next(iter(fams)), noun, kind=kind)
    return f"Balanced {noun}"


def line_identity(role_keys: list[str | None], *, kind: str | None = None) -> str | None:
    """Name a unit from selected roles (Checking line, Shutdown pair, …)."""
    keys = [str(k) for k in role_keys if k]
    if not keys:
        return None
    noun = _unit_noun(kind)
    if _count_roles(keys, _PUNISHING_ROLES) >= 2:
        return f"Punishing {noun}"
    if _count_roles(keys, _SHUTDOWN_ROLES) >= 2:
        return f"Shutdown {noun}"
    if _count_roles(keys, _SHOOTING_ROLES) >= 2:
        return f"Shooting {noun}"
    if _count_roles(keys, _FORECHECK_ROLES) >= 2:
        return f"Forechecking {noun}"
    if _count_roles(keys, _PLAYMAKING_ROLES) >= 2:
        return f"Playmaking {noun}" if noun != "unit" or (kind or "").strip().lower() != "powerplay" else f"Setup {noun}"
    if _count_roles(keys, _SPEED_ROLES) >= 2:
        if _count_roles(keys, _PHYSICAL_ROLES):
            return f"Forechecking {noun}"
        if _count_roles(keys, frozenset({"counterattacking"})) >= _count_roles(keys, frozenset({"speedy_forward"})):
            return f"Transition {noun}"
        return f"Speed {noun}"
    if _count_roles(keys, _NETFRONT_ROLES) >= 2:
        return f"Net-front {noun}"

    counts: Counter[str] = Counter()
    for key in keys:
        fam = _role_family(key, kind)
        if fam:
            counts[fam] += 1
    if not counts:
        return f"Balanced {noun}"
    ranked = counts.most_common()
    top_fam, top_n = ranked[0]
    if top_n >= 2:
        return _family_label(top_fam, noun, kind=kind)
    return _combo_label({fam for fam, _n in ranked}, noun, kind=kind)


def line_ability_grade(
    ability: float | None,
    *,
    kind: str | None = None,
    role_keys: list[str | None] | None = None,
) -> dict[str, Any] | None:
    """Ability score plus a makeup label from the selected roles."""
    if ability is None:
        return None
    score = float(ability)
    if score >= 85:
        key = "1st"
    elif score >= 76:
        key = "2nd"
    elif score >= 68:
        key = "3rd"
    elif score >= 60:
        key = "4th"
    else:
        key = "depth"
    label = line_identity(list(role_keys or []), kind=kind)
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
