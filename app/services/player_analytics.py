"""Player profile Analytics panel: role tiers, usage, assignments, trends, coach notes."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import Player, PlayerGoalieStat, PlayerSkaterStat, Season, Team
from app.services.player_overall_score import compute_player_overall_100
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row

SKATER_ROLE_TIERS: tuple[str, ...] = (
    "Depth",
    "Bottom Six",
    "Middle Six",
    "Top Line",
    "Star",
    "Superstar",
)
GOALIE_ROLE_TIERS: tuple[str, ...] = (
    "Not Qualified",
    "Depth",
    "Backup",
    "Starting",
    "Superstar",
)

_MENTAL_TEAMMATE_KEYS: tuple[str, ...] = (
    "determination",
    "teamplayer",
    "character",
    "leadership",
    "temperament",
    "professionalism",
)

_team_lines_cache: dict[str, tuple[float, dict[int, dict[str, Any]]]] = {}


def _float_cell(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _fmt_toi(seconds: int | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_toi_per_game(total_seconds: int | None, gp: int) -> str | None:
    if not gp or total_seconds is None:
        return None
    return _fmt_toi(int(round(total_seconds / gp)))


def _rating_to_pct(val: float | None, *, lo: float = 0.0, hi: float = 20.0) -> float:
    if val is None:
        return 0.0
    return max(0.0, min(100.0, ((float(val) - lo) / (hi - lo)) * 100.0))


def _load_team_lines(raw_dir: Path) -> dict[int, dict[str, Any]]:
    path = raw_dir / "team_lines.csv"
    if not path.is_file():
        return {}
    path_key = str(path.resolve())
    mtime = path.stat().st_mtime
    ent = _team_lines_cache.get(path_key)
    if ent is not None and ent[0] == mtime:
        return ent[1]

    from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

    df = read_csv_normalized(path)
    by_team: dict[int, dict[str, Any]] = {}
    if df.empty or "teamid" not in df.columns:
        _team_lines_cache[path_key] = (mtime, by_team)
        return by_team

    slot_cols: list[tuple[str, str, str]] = []
    for col in df.columns:
        if col == "teamid":
            continue
        m = re.match(
            r"^(es|pp[a-z0-9]*|pk[a-z0-9]*|4on4|3on3|shootout|goalie|extra_attacker)_",
            col,
            re.I,
        )
        if m:
            slot_cols.append((col, m.group(1).lower(), col))

    for _, row in df.iterrows():
        r = row.to_dict()
        tid = to_int(cell_val(r, "teamid"))
        if tid is None:
            continue
        assignments: dict[str, str] = {}
        for col, group, _ in slot_cols:
            raw = cell_val(r, col)
            if raw is None or str(raw).strip() == "":
                continue
            assignments[col] = str(raw).strip()
        by_team[int(tid)] = {"assignments": assignments, "raw": r}

    _team_lines_cache[path_key] = (mtime, by_team)
    return by_team


def _player_fhm_str(player: Player) -> str | None:
    fid = player.fhm_player_id
    if fid is None:
        return None
    return str(fid).strip()


def _find_player_assignments(
    team_lines: dict[int, dict[str, Any]],
    team_fhm_id: int | None,
    player_fhm: str,
) -> list[dict[str, str]]:
    if team_fhm_id is None or team_fhm_id not in team_lines:
        return []
    assigns = team_lines[team_fhm_id].get("assignments") or {}
    out: list[dict[str, str]] = []
    for col, pid in assigns.items():
        if pid == player_fhm:
            label = _assignment_label(col)
            unit_key, unit_title = _assignment_unit(col)
            out.append(
                {
                    "column": col,
                    "label": label,
                    "group": _assignment_group(col),
                    "unit_key": unit_key,
                    "unit_title": unit_title,
                    "assignment": _assignment_card_text(col),
                    "sort_key": _assignment_sort_key(col),
                }
            )
    return sorted(out, key=lambda a: a.get("sort_key") or (99, 99, ""))


def _team_line_team_for_player(
    team_lines: dict[int, dict[str, Any]],
    player_fhm: str,
    preferred_team_fhm_id: int | None,
) -> int | None:
    if not player_fhm:
        return preferred_team_fhm_id
    if preferred_team_fhm_id is not None:
        assigns = team_lines.get(preferred_team_fhm_id, {}).get("assignments") or {}
        if any(pid == player_fhm for pid in assigns.values()):
            return preferred_team_fhm_id
    for team_id, data in team_lines.items():
        assigns = data.get("assignments") or {}
        if any(pid == player_fhm for pid in assigns.values()):
            return team_id
    return preferred_team_fhm_id


def _assignment_group(col: str) -> str:
    c = col.lower()
    if c.startswith("es_"):
        return "Even strength"
    if c.startswith("pp"):
        return "Power play"
    if c.startswith("pk"):
        return "Penalty kill"
    if c.startswith("goalie"):
        return "Goalie"
    if "shootout" in c:
        return "Shootout"
    if "extra_attacker" in c:
        return "Extra attacker"
    if "4on4" in c or "3on3" in c:
        return "Special teams"
    return "Other"


def _assignment_unit(col: str) -> tuple[str, str]:
    c = col.lower()
    if c.startswith("es_"):
        return ("even_strength", "Even Strength")
    if c.startswith("pp"):
        return ("powerplay", "Powerplay")
    if c.startswith("pk"):
        return ("penalty_kill", "Penalty Kill")
    if c.startswith("goalie"):
        return ("goalie", "Goalie")
    if c.startswith("4on4") or c.startswith("3on3"):
        return ("special", "Special Teams")
    if c.startswith("shootout"):
        return ("shootout", "Shootout")
    return ("other", "Other")


def _ordinal(n: int | str) -> str:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 10 <= (i % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def _assignment_sort_key(col: str) -> tuple[int, int, str]:
    c = col.lower()
    unit_order = {
        "even_strength": 0,
        "powerplay": 1,
        "penalty_kill": 2,
        "goalie": 3,
        "special": 4,
        "shootout": 5,
        "other": 6,
    }
    unit_key, _ = _assignment_unit(c)
    m = re.search(r"_l(\d+)_", c)
    line = int(m.group(1)) if m else 99
    return (unit_order.get(unit_key, 99), line, c)


def _assignment_card_text(col: str) -> str:
    c = col.lower()
    if c.startswith("goalie_"):
        n = c.replace("goalie_", "").strip()
        return f"Goalie {n}" if n else "Goalie"
    if c.startswith("shootout_"):
        n = c.replace("shootout_", "").strip()
        return f"Shootout {n}" if n else "Shootout"
    m = re.match(r"^(es|pp[a-z0-9]*|pk[a-z0-9]*|4on4|3on3)_l(\d+)_(\w+)$", c)
    if not m:
        return _assignment_label(col)
    unit_raw = m.group(1)
    line = _ordinal(m.group(2))
    pos = m.group(3).upper()
    pos_map = {"LW": "LW", "C": "C", "RW": "RW", "LD": "D1", "RD": "D2", "F1": "F1", "F2": "F2"}
    pos = pos_map.get(pos, pos)
    if unit_raw == "es":
        suffix = "line"
    elif unit_raw.startswith("pp"):
        suffix = "Unit"
    elif unit_raw.startswith("pk"):
        suffix = "Unit"
    else:
        suffix = "Group"
    return f"{pos} - {line} {suffix}"


def _assignment_label(col: str) -> str:
    c = col.lower()
    if c.startswith("goalie_"):
        n = c.replace("goalie_", "").strip()
        return f"Goalie {n.title()}" if n.isdigit() else "Goalie"
    m = re.match(r"^(es|pp[a-z0-9]*|pk[a-z0-9]*)_l(\d+)_(\w+)$", c)
    if m:
        kind_raw = m.group(1)
        if kind_raw == "es":
            kind = "ES"
        elif kind_raw.startswith("pp"):
            kind = "PP"
        elif kind_raw.startswith("pk"):
            kind = "PK"
        else:
            kind = kind_raw.upper()
        line = m.group(2)
        pos = m.group(3).upper()
        pos_map = {
            "lw": "LW",
            "c": "C",
            "rw": "RW",
            "ld": "LD",
            "rd": "RD",
            "f1": "F1",
            "f2": "F2",
        }
        pos = pos_map.get(pos, pos)
        return f"{kind} L{line} {pos}"
    return col.replace("_", " ").title()


def _deployment_groups(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = [
        ("even_strength", "Even Strength"),
        ("powerplay", "Powerplay"),
        ("penalty_kill", "Penalty Kill"),
    ]
    by_key: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        key = str(assignment.get("unit_key") or "other")
        title = str(assignment.get("unit_title") or "Other")
        by_key.setdefault(key, {"key": key, "title": title, "assignments": []})
        by_key[key]["assignments"].append(assignment)
    out = [by_key.pop(key, {"key": key, "title": title, "assignments": []}) for key, title in preferred]
    out.extend(by_key.values())
    return out


def _linemates_from_assignments(
    team_lines: dict[int, dict[str, Any]],
    team_fhm_id: int | None,
    player_fhm: str,
    *,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    if team_fhm_id is None or team_fhm_id not in team_lines:
        return []
    assigns = team_lines[team_fhm_id].get("assignments") or {}
    my_cols = [c for c, pid in assigns.items() if pid == player_fhm]
    if not my_cols:
        return []
    mates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for col in my_cols:
        m = re.match(r"^(es|pp[a-z0-9]*|pk[a-z0-9]*|4on4|3on3)_l(\d+)_", col.lower())
        if not m:
            continue
        prefix = m.group(0)
        for oc, opid in assigns.items():
            if oc == col or opid == player_fhm:
                continue
            if not oc.startswith(prefix):
                continue
            key = (prefix, str(opid))
            if key in seen:
                continue
            seen.add(key)
            slot_label = _assignment_label(oc)
            unit_key, unit_title = _assignment_unit(oc)
            summary = _resolve_player_summary(session, opid)
            chemistry = _chemistry_fit(_resolve_player_summary(session, player_fhm), summary, current=True)
            mates.append(
                {
                    "slot": slot_label,
                    "assigned": _assignment_card_text(oc),
                    "fhm_player_id": opid,
                    "player": summary["player"],
                    "name": summary["name"],
                    "position": summary["position"],
                    "overall": summary["overall"],
                    "team_player": summary["team_player"],
                    "passing": summary["passing"],
                    "getting_open": summary["getting_open"],
                    "chemistry": chemistry,
                    "unit_key": unit_key,
                    "unit_title": unit_title,
                    "sort_key": _assignment_sort_key(oc),
                }
            )
    return sorted(mates, key=lambda m: m.get("sort_key") or (99, 99, ""))[:12]


def _linemate_groups(linemates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = [
        ("even_strength", "Even Strength Linemates"),
        ("powerplay", "Powerplay Linemates"),
        ("penalty_kill", "Penalty Kill Linemates"),
    ]
    by_key: dict[str, dict[str, Any]] = {}
    for mate in linemates:
        key = str(mate.get("unit_key") or "other")
        title = str(mate.get("unit_title") or "Other")
        by_key.setdefault(key, {"key": key, "title": f"{title} Linemates", "mates": []})
        by_key[key]["mates"].append(mate)
    out: list[dict[str, Any]] = []
    for key, title in wanted:
        group = by_key.pop(key, None)
        if group and group["mates"]:
            group["title"] = title
            out.append(group)
    out.extend(group for group in by_key.values() if group["mates"])
    return out


def _resolve_player_name(session: Session | None, fhm_id: str) -> str:
    if not session or not fhm_id:
        return f"Player #{fhm_id}"
    pl = session.scalars(
        select(Player).where(Player.fhm_player_id == str(fhm_id)).limit(1)
    ).first()
    return (pl.full_name or f"Player #{fhm_id}") if pl else f"Player #{fhm_id}"


def _resolve_player_summary(session: Session | None, fhm_id: str) -> dict[str, Any]:
    pl: Player | None = None
    if session and fhm_id:
        pl = session.scalars(
            select(Player).where(Player.fhm_player_id == str(fhm_id)).limit(1)
        ).first()
    rr = get_player_ratings_row(fhm_id)
    abi = getattr(pl, "overall_ability", None) if pl else fhm_abi_pot_float(rr.get("ability") if rr else None)
    pot = getattr(pl, "overall_potential", None) if pl else fhm_abi_pot_float(rr.get("potential") if rr else None)
    is_goalie = (getattr(pl, "position", "") or "").strip().upper() == "G" if pl else False
    overall = compute_player_overall_100(abi, pot, rr, is_goalie=is_goalie)
    return {
        "player": pl,
        "name": (pl.full_name or f"Player #{fhm_id}") if pl else f"Player #{fhm_id}",
        "position": (pl.position or "").strip().upper() if pl and pl.position else "",
        "overall": overall,
        "shooting": _int_rating(rr, "shooting"),
        "playmaking": _int_rating(rr, "playmaking"),
        "team_player": _int_rating(rr, "teamplayer"),
        "leadership": _int_rating(rr, "leadership"),
        "passing": _int_rating(rr, "passing"),
        "getting_open": _int_rating(rr, "getting_open"),
    }


def _int_rating(row: dict | None, key: str) -> int | None:
    v = _float_cell(row.get(key) if row else None)
    return int(round(v)) if v is not None else None


def _chemistry_fit(anchor: dict[str, Any], mate: dict[str, Any], *, current: bool) -> dict[str, Any]:
    """Transparent heuristic from imported ratings, not hidden AI."""
    values: list[float] = []
    reasons: list[str] = []

    anchor_passing = anchor.get("passing") or anchor.get("playmaking")
    mate_open = mate.get("getting_open") or mate.get("shooting")
    if anchor_passing is not None and mate_open is not None:
        values.append((float(anchor_passing) + float(mate_open)) / 2.0)
        reasons.append("receives playmaking")

    mate_passing = mate.get("passing") or mate.get("playmaking")
    anchor_open = anchor.get("getting_open") or anchor.get("shooting")
    if mate_passing is not None and anchor_open is not None:
        values.append((float(mate_passing) + float(anchor_open)) / 2.0)
        reasons.append("can return chances")

    if anchor.get("team_player") is not None and mate.get("team_player") is not None:
        values.append((float(anchor["team_player"]) + float(mate["team_player"])) / 2.0)
        reasons.append("team-first fit")

    if mate.get("overall") is not None:
        values.append(max(0.0, min(20.0, float(mate["overall"]) / 5.0)))

    if not values:
        score = 50
    else:
        score = int(round(max(35.0, min(98.0, (sum(values) / len(values)) * 5.0))))
    if current:
        score = min(99, score + 4)
    if score >= 86:
        label = "Excellent fit"
    elif score >= 74:
        label = "Strong fit"
    elif score >= 62:
        label = "Useful fit"
    else:
        label = "Developing fit"
    return {"score": score, "label": label, "reasons": reasons[:2]}


def _chemistry_candidates(
    team_lines: dict[int, dict[str, Any]],
    team_fhm_id: int | None,
    player_fhm: str,
    *,
    session: Session | None = None,
    current_linemates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if team_fhm_id is None or team_fhm_id not in team_lines or not player_fhm:
        return []
    assigns = team_lines[team_fhm_id].get("assignments") or {}
    current_ids = {str(m.get("fhm_player_id")) for m in (current_linemates or [])}
    anchor = _resolve_player_summary(session, player_fhm)
    candidates: dict[str, dict[str, Any]] = {}
    for col, opid in assigns.items():
        opid_s = str(opid)
        if not opid_s or opid_s == player_fhm or opid_s in candidates:
            continue
        summary = _resolve_player_summary(session, opid_s)
        chemistry = _chemistry_fit(anchor, summary, current=opid_s in current_ids)
        candidates[opid_s] = {
            "fhm_player_id": opid_s,
            "player": summary["player"],
            "name": summary["name"],
            "position": summary["position"],
            "assigned": _assignment_card_text(col),
            "unit_title": _assignment_unit(col)[1],
            "overall": summary["overall"],
            "team_player": summary["team_player"],
            "passing": summary["passing"],
            "getting_open": summary["getting_open"],
            "chemistry": chemistry,
            "current_linemate": opid_s in current_ids,
        }
    return sorted(
        candidates.values(),
        key=lambda c: (
            int(c.get("current_linemate") or False),
            int((c.get("chemistry") or {}).get("score") or 0),
        ),
        reverse=True,
    )[:6]


def _chemistry_candidates_from_team_roster(
    session: Session,
    player: Player,
    team: Team | None,
    *,
    current_linemates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fallback chemistry fits when a player is not present in imported line assignments."""
    if team is None or player.id is None:
        return []
    anchor_fhm = _player_fhm_str(player) or ""
    anchor = _resolve_player_summary(session, anchor_fhm)
    if anchor.get("name", "").startswith("Player #"):
        anchor = {
            "player": player,
            "name": player.full_name,
            "position": (player.position or "").strip().upper(),
            "overall": None,
            "shooting": None,
            "playmaking": None,
            "team_player": None,
            "leadership": None,
            "passing": None,
            "getting_open": None,
        }
        rr = get_player_ratings_row(getattr(player, "fhm_player_id", None))
        if rr:
            abi = getattr(player, "overall_ability", None)
            pot = getattr(player, "overall_potential", None)
            anchor.update(
                {
                    "overall": compute_player_overall_100(
                        abi,
                        pot,
                        rr,
                        is_goalie=((player.position or "").strip().upper() == "G"),
                    ),
                    "shooting": _int_rating(rr, "shooting"),
                    "playmaking": _int_rating(rr, "playmaking"),
                    "team_player": _int_rating(rr, "teamplayer"),
                    "leadership": _int_rating(rr, "leadership"),
                    "passing": _int_rating(rr, "passing"),
                    "getting_open": _int_rating(rr, "getting_open"),
                }
            )
    current_ids = {str(m.get("fhm_player_id")) for m in (current_linemates or [])}
    rows = session.scalars(
        select(Player)
        .where(
            Player.current_team_id == int(team.id),
            Player.id != int(player.id),
            Player.retired.is_(False),
        )
        .limit(80)
    ).all()
    candidates: list[dict[str, Any]] = []
    for mate in rows:
        if (mate.position or "").strip().upper().startswith("G"):
            continue
        fhm = _player_fhm_str(mate) or ""
        if not fhm or fhm == anchor_fhm:
            continue
        summary = _resolve_player_summary(session, fhm)
        chemistry = _chemistry_fit(anchor, summary, current=fhm in current_ids)
        candidates.append(
            {
                "fhm_player_id": fhm,
                "player": summary["player"] or mate,
                "name": summary["name"] or mate.full_name,
                "position": summary["position"],
                "assigned": "Roster fit",
                "unit_title": "Team Roster",
                "overall": summary["overall"],
                "team_player": summary["team_player"],
                "passing": summary["passing"],
                "getting_open": summary["getting_open"],
                "chemistry": chemistry,
                "current_linemate": fhm in current_ids,
            }
        )
    return sorted(
        candidates,
        key=lambda c: (
            int(c.get("current_linemate") or False),
            int((c.get("chemistry") or {}).get("score") or 0),
            int(c.get("overall") or 0),
        ),
        reverse=True,
    )[:6]


def _primary_position_rating(position_rows: list[dict[str, object]]) -> float | None:
    best: float | None = None
    for row in position_rows:
        v = row.get("value")
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if best is None or fv > best:
            best = fv
    return best


def _skater_role_index(
    *,
    pos_rating: float | None,
    abi: float | None,
    pot: float | None,
    ovr: int | None,
    toi_pg_sec: int | None,
    ppg: float | None,
    assignments: list[dict[str, str]],
) -> int:
    score = 0.0
    if pos_rating is not None:
        score += pos_rating * 2.2
    if abi is not None:
        score += abi * 4.0
    if pot is not None:
        score += pot * 1.5
    if ovr is not None:
        score += ovr * 0.35
    if toi_pg_sec is not None:
        mpg = toi_pg_sec / 60.0
        if mpg >= 20:
            score += 18
        elif mpg >= 17:
            score += 12
        elif mpg >= 15:
            score += 6
    if ppg is not None:
        if ppg >= 1.0:
            score += 14
        elif ppg >= 0.7:
            score += 8
        elif ppg >= 0.45:
            score += 4
    for a in assignments:
        lbl = a.get("label") or ""
        if re.search(r"ES L1", lbl, re.I):
            score += 10
        elif re.search(r"ES L2", lbl, re.I):
            score += 6
        elif re.search(r"PP", lbl):
            score += 4
    idx = 0
    if score >= 78:
        idx = 4
    elif score >= 62:
        idx = 3
    elif score >= 46:
        idx = 2
    elif score >= 30:
        idx = 1
    return min(idx, len(SKATER_ROLE_TIERS) - 1)


def _goalie_role_index(
    *,
    pos_rating: float | None,
    abi: float | None,
    pot: float | None,
    gp: int,
    gs: int | None,
    minutes: int | None,
    assignments: list[dict[str, str]],
    sv_pct: float | None,
    gr: float | None,
) -> int:
    is_g1 = any("Goalie 1" in (a.get("label") or "") for a in assignments)
    is_g2 = any("Goalie 2" in (a.get("label") or "") for a in assignments)
    if gp <= 0 and not is_g1 and not is_g2:
        return 0
    score = 0.0
    if is_g1:
        score += 55
    elif is_g2:
        score += 38
    elif gp > 0:
        score += 12
    if pos_rating is not None:
        score += pos_rating * 1.8
    if abi is not None:
        score += abi * 3.5
    if pot is not None:
        score += pot * 1.2
    if gp > 0 and gs is not None:
        ratio = gs / gp
        if ratio >= 0.75:
            score += 22
        elif ratio >= 0.55:
            score += 14
        elif ratio >= 0.35:
            score += 6
    if minutes and gp > 0:
        mpg = minutes / gp
        if mpg >= 55:
            score += 12
        elif mpg >= 45:
            score += 6
    if sv_pct is not None:
        if sv_pct >= 0.915:
            score += 8
        elif sv_pct >= 0.9:
            score += 4
    if gr is not None:
        if gr >= 70:
            score += 6
        elif gr >= 60:
            score += 3
    if score >= 85:
        return 4
    if score >= 65:
        return 3
    if score >= 45:
        return 2
    if score >= 25:
        return 1
    return 0


def _role_tier_bar(tiers: tuple[str, ...], active_index: int) -> list[dict[str, Any]]:
    n = len(tiers)
    out: list[dict[str, Any]] = []
    for i, name in enumerate(tiers):
        pct = 100.0 / n
        out.append(
            {
                "name": name,
                "active": i == active_index,
                "width_pct": pct,
                "label": _tier_short_label(name, i, n),
            }
        )
    return out


def _tier_short_label(name: str, index: int, total: int) -> str:
    if name == "Not Qualified":
        return "N/Q"
    if name == "Superstar":
        return f"Top {max(6, total)}"
    if name == "Star":
        return "Star"
    if name == "Top Line":
        return "Top line"
    if name == "Starting":
        return "Starter"
    if name == "Backup":
        return "Backup"
    if name == "Depth":
        return "Depth"
    if name == "Bottom Six":
        return "3rd line"
    if name == "Middle Six":
        return "2nd line"
    return name


def _mental_teammate_score(rr: dict | None, keys: tuple[str, ...]) -> int | None:
    vals: list[float] = []
    for k in keys:
        v = _float_cell(rr.get(k) if rr else None)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    return int(round(40.0 + (avg / 20.0) * 60.0))


def _mental_chips(rr: dict | None, keys: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, key in keys:
        v = _float_cell(rr.get(key) if rr else None)
        out.append({"label": label, "value": int(round(v)) if v is not None else None})
    return out


def _mental_profile(rr: dict | None) -> dict[str, Any]:
    score = _mental_teammate_score(rr, _MENTAL_TEAMMATE_KEYS)
    chips = _mental_chips(
        rr,
        (
            ("Determination", "determination"),
            ("Team Player", "teamplayer"),
            ("Character", "character"),
            ("Leadership", "leadership"),
            ("Temperament", "temperament"),
            ("Professionalism", "professionalism"),
        ),
    )
    leadership_raw = _float_cell(rr.get("leadership") if rr else None)
    leadership_pct = _rating_to_pct(leadership_raw)
    if leadership_raw is None:
        leadership_label = "Unknown"
    elif leadership_raw >= 18:
        leadership_label = "Elite Leader"
    elif leadership_raw >= 15:
        leadership_label = "Strong Voice"
    elif leadership_raw >= 12:
        leadership_label = "Support Leader"
    elif leadership_raw >= 9:
        leadership_label = "Quiet Contributor"
    else:
        leadership_label = "Still Developing"

    capability_specs = (
        ("Room Trust", ("teamplayer", "character", "professionalism")),
        ("Composure", ("temperament", "determination")),
        ("Accountability", ("determination", "professionalism")),
        ("Captaincy", ("leadership", "teamplayer", "character")),
    )
    capabilities: list[dict[str, Any]] = []
    for label, keys in capability_specs:
        vals = [_float_cell(rr.get(k) if rr else None) for k in keys]
        valid = [v for v in vals if v is not None]
        avg = (sum(valid) / len(valid)) if valid else None
        capabilities.append(
            {
                "label": label,
                "value": int(round(avg)) if avg is not None else None,
                "pct": _rating_to_pct(avg),
            }
        )

    return {
        "score": score,
        "chips": chips,
        "leadership": {
            "label": leadership_label,
            "value": int(round(leadership_raw)) if leadership_raw is not None else None,
            "pct": leadership_pct,
            "tiers": ("None", "Alternate", "Light", "Moderate", "Strong", "Elite"),
        },
        "capabilities": capabilities,
    }


def _recent_form_skater(game_log: list[Any]) -> dict[str, Any]:
    rows = game_log[:10]
    if not rows:
        return {"label": "—", "pts": None, "gr": None, "games": 0}
    pts = 0
    gr_sum = 0.0
    gr_n = 0
    for line in rows:
        g = int(getattr(line, "goals", 0) or 0)
        a = int(getattr(line, "assists", 0) or 0)
        pts += g + a
        gr = getattr(line, "game_rating", None)
        if gr is not None:
            gr_sum += float(gr)
            gr_n += 1
    label = "Stable"
    if pts >= 8:
        label = "Hot"
    elif pts >= 5:
        label = "Rolling"
    elif pts <= 2 and len(rows) >= 3:
        label = "Cold"
    return {
        "label": label,
        "pts": pts,
        "gr": round(gr_sum / gr_n, 1) if gr_n else None,
        "games": len(rows),
    }


def _recent_form_goalie(game_log: list[Any]) -> dict[str, Any]:
    rows = game_log[:10]
    if not rows:
        return {"label": "—", "sv_pct": None, "gr": None, "games": 0}
    sv_total = 0
    sa_total = 0
    gr_sum = 0.0
    gr_n = 0
    for line in rows:
        sa = int(getattr(line, "shots_against", 0) or 0)
        sv = int(getattr(line, "saves", 0) or 0)
        sa_total += sa
        sv_total += sv
        gr = getattr(line, "game_rating", None)
        if gr is not None:
            gr_sum += float(gr)
            gr_n += 1
    sv_pct = (sv_total / sa_total) if sa_total else None
    label = "Stable"
    if sv_pct is not None:
        if sv_pct >= 0.93:
            label = "Hot"
        elif sv_pct >= 0.9:
            label = "Rolling"
        elif sv_pct < 0.88:
            label = "Cold"
    return {
        "label": label,
        "sv_pct": round(sv_pct, 3) if sv_pct is not None else None,
        "gr": round(gr_sum / gr_n, 1) if gr_n else None,
        "games": len(rows),
    }


def _trajectory_summary(trend_rows: list[dict[str, Any]], *, goalie_mode: bool) -> dict[str, Any]:
    if not trend_rows:
        return {"spark": [], "points": [], "peak_label": None, "direction": "—"}
    if goalie_mode:
        values = [float(r.get("gk_w") or 0) for r in trend_rows]
        peak_key = "gk_w"
    else:
        values = [float(r.get("pts") or 0) for r in trend_rows]
        peak_key = "pts"
    peak_i = max(range(len(values)), key=lambda i: values[i])
    max_v = max(values) if values else 0
    spark = [{"v": v} for v in values]
    points = []
    for r, value in zip(trend_rows, values):
        pct = int(round((value / max_v) * 100.0)) if max_v else 0
        points.append(
            {
                "label": r.get("label"),
                "value": int(value) if float(value).is_integer() else round(value, 1),
                "pct": pct,
            }
        )
    direction = "Stable"
    direction_detail = "Production is holding close to the player's earlier level."
    if len(values) >= 2:
        previous = values[-2]
        if values[-1] > previous + 2:
            direction = "Rising"
            direction_detail = "The latest season is trending upward from the previous season."
        elif values[-1] < previous - 2:
            direction = "Cooling"
            direction_detail = "The latest season has dipped from the previous season."
        elif values[-1] >= max_v - 1:
            direction = "At peak"
            direction_detail = "The current season is at or near the player's best imported season."
    latest = points[-1] if points else None
    previous = points[-2] if len(points) >= 2 else None
    return {
        "spark": spark,
        "points": points[-6:],
        "latest": latest,
        "previous": previous,
        "peak_label": trend_rows[peak_i].get("label"),
        "peak_value": values[peak_i],
        "direction": direction,
        "direction_detail": direction_detail,
        "metric_label": "Wins" if goalie_mode else "Points",
        "peak_key": peak_key,
    }


def _coach_notes_skater(
    *,
    rr: dict | None,
    season_stat: PlayerSkaterStat | None,
    assignments: list[dict[str, str]],
    form: dict[str, Any],
    cat: dict[str, float | None],
) -> list[str]:
    notes: list[str] = []
    if any("PP" in (a.get("label") or "") for a in assignments):
        notes.append("Power-play usage")
    if season_stat and (season_stat.ppto_seconds or 0) > 0:
        notes.append("Special-teams minutes")
    if season_stat and (season_stat.plus_minus or 0) >= 15:
        notes.append("Positive impact")
    elif season_stat and (season_stat.plus_minus or 0) <= -10:
        notes.append("Needs sheltering")
    off = cat.get("off")
    def_ = cat.get("def")
    if off is not None and def_ is not None and off >= 16 and def_ >= 15:
        notes.append("Two-way driver")
    elif off is not None and off >= 16:
        notes.append("Offensive catalyst")
    elif def_ is not None and def_ >= 15:
        notes.append("Defensive support")
    if form.get("label") == "Hot":
        notes.append("Hot hand")
    if not notes:
        notes.append("Balanced contributor")
    return notes[:4]


def _coach_notes_goalie(
    *,
    season_stat: PlayerGoalieStat | None,
    assignments: list[dict[str, str]],
    form: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    if any("Goalie 1" in (a.get("label") or "") for a in assignments):
        notes.append("Primary starter")
    elif any("Goalie 2" in (a.get("label") or "") for a in assignments):
        notes.append("Reliable backup")
    if season_stat and season_stat.gp > 0 and (season_stat.games_started or 0) >= season_stat.gp * 0.7:
        notes.append("Workhorse starter")
    if season_stat and (season_stat.so or 0) >= 3:
        notes.append("Clutch shutouts")
    if form.get("label") == "Hot":
        notes.append("Hot hand")
    if form.get("label") == "Cold":
        notes.append("Needs support")
    if not notes:
        notes.append("Steady crease presence")
    return notes[:4]


def build_player_analytics_panel(
    session: Session,
    player: Player,
    *,
    ratings_row: dict | None,
    season: Season | None,
    is_goalie: bool,
    use_goalie_game_log: bool,
    game_log: list[Any],
    position_ratings_rows: list[dict[str, object]],
    hero_abi: float | None,
    hero_pot: float | None,
    player_ovr: int | None,
    season_trend_rows: list[dict[str, Any]],
    goalie_trend_mode: bool,
    team_context: Team | None = None,
    retired: bool = False,
) -> dict[str, Any]:
    if retired:
        return {
            "enabled": False,
            "summary_meta": "Retired player",
            "is_goalie": is_goalie,
        }

    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    context_team = team_context or player.current_team
    team_fhm = None
    if context_team and context_team.fhm_team_id:
        try:
            team_fhm = int(str(context_team.fhm_team_id).strip())
        except (TypeError, ValueError):
            team_fhm = None
    player_fhm = _player_fhm_str(player)
    team_lines = _load_team_lines(raw_dir)
    team_fhm = _team_line_team_for_player(team_lines, player_fhm or "", team_fhm)
    assignments = _find_player_assignments(team_lines, team_fhm, player_fhm or "")
    pos_rating = _primary_position_rating(position_ratings_rows)

    season_stat_gk: PlayerGoalieStat | None = None
    season_stat_sk: PlayerSkaterStat | None = None
    if season and not retired:
        if is_goalie or use_goalie_game_log:
            season_stat_gk = session.scalars(
                select(PlayerGoalieStat).where(
                    PlayerGoalieStat.player_id == player.id,
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == "rs",
                ).limit(1)
            ).first()
        else:
            season_stat_sk = session.scalars(
                select(PlayerSkaterStat).where(
                    PlayerSkaterStat.player_id == player.id,
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.stat_segment == "rs",
                ).limit(1)
            ).first()

    cat_sk = skater_category_averages(ratings_row) if ratings_row else {}
    cat_gk = goalie_category_averages(ratings_row) if ratings_row else {}

    if is_goalie or use_goalie_game_log:
        gp = int(season_stat_gk.gp or 0) if season_stat_gk else 0
        gs = int(season_stat_gk.games_started or 0) if season_stat_gk else None
        minutes = int(season_stat_gk.minutes_played or 0) if season_stat_gk else None
        sv_pct = float(season_stat_gk.sv_pct) if season_stat_gk and season_stat_gk.sv_pct is not None else None
        if sv_pct is None and season_stat_gk and season_stat_gk.sa:
            sv_pct = (season_stat_gk.sa - season_stat_gk.ga) / season_stat_gk.sa
        gr = float(season_stat_gk.game_rating) if season_stat_gk and season_stat_gk.game_rating is not None else None
        role_idx = _goalie_role_index(
            pos_rating=pos_rating,
            abi=hero_abi,
            pot=hero_pot,
            gp=gp,
            gs=gs,
            minutes=minutes,
            assignments=assignments,
            sv_pct=sv_pct,
            gr=gr,
        )
        role_name = GOALIE_ROLE_TIERS[role_idx]
        form = _recent_form_goalie(game_log)
        return {
            "enabled": True,
            "is_goalie": True,
            "summary_meta": f"Role: {role_name} · Form: {form.get('label', '—')}",
            "role_title": role_name,
            "role_tiers": _role_tier_bar(GOALIE_ROLE_TIERS, role_idx),
            "mental": _mental_profile(ratings_row),
            "crease_profile": _goalie_crease_profile(season_stat_gk, cat_gk, gr),
            "assignments": assignments,
            "deployment_groups": _deployment_groups(assignments),
            "linemates": [],
            "linemate_groups": [],
            "chemistry_candidates": [],
            "trajectory": _trajectory_summary(season_trend_rows, goalie_mode=goalie_trend_mode),
            "coach_notes": _coach_notes_goalie(
                season_stat=season_stat_gk,
                assignments=assignments,
                form=form,
            ),
            "skill_dna": _goalie_skill_dna(cat_gk, form),
        }

    # Skater path
    linemates = _linemates_from_assignments(
        team_lines, team_fhm, player_fhm or "", session=session
    )
    chemistry_candidates = _chemistry_candidates(
        team_lines,
        team_fhm,
        player_fhm or "",
        session=session,
        current_linemates=linemates,
    )
    if not chemistry_candidates:
        chemistry_candidates = _chemistry_candidates_from_team_roster(
            session,
            player,
            context_team,
            current_linemates=linemates,
        )
    gp = int(season_stat_sk.gp or 0) if season_stat_sk else 0
    toi_pg = None
    ppg = None
    if season_stat_sk and gp > 0:
        if season_stat_sk.toi_seconds:
            toi_pg = int(round(season_stat_sk.toi_seconds / gp))
        pts = int(season_stat_sk.points or (season_stat_sk.goals + season_stat_sk.assists))
        ppg = pts / gp
    role_idx = _skater_role_index(
        pos_rating=pos_rating,
        abi=hero_abi,
        pot=hero_pot,
        ovr=player_ovr,
        toi_pg_sec=toi_pg,
        ppg=ppg,
        assignments=assignments,
    )
    role_name = SKATER_ROLE_TIERS[role_idx]
    form = _recent_form_skater(game_log)
    return {
        "enabled": True,
        "is_goalie": False,
        "summary_meta": f"Role: {role_name} · Form: {form.get('label', '—')}",
        "role_title": role_name,
        "role_tiers": _role_tier_bar(SKATER_ROLE_TIERS, role_idx),
        "mental": _mental_profile(ratings_row),
        "crease_profile": None,
        "skill_dna": _skater_skill_dna(cat_sk, ratings_row),
        "usage": _skater_usage(season_stat_sk),
        "assignments": assignments,
        "deployment_groups": _deployment_groups(assignments),
        "linemates": linemates,
        "linemate_groups": _linemate_groups(linemates),
        "chemistry_candidates": chemistry_candidates,
        "trajectory": _trajectory_summary(season_trend_rows, goalie_mode=False),
        "coach_notes": _coach_notes_skater(
            rr=ratings_row,
            season_stat=season_stat_sk,
            assignments=assignments,
            form=form,
            cat=cat_sk,
        ),
    }


def _skater_skill_dna(cat: dict[str, float | None], rr: dict | None) -> list[dict[str, Any]]:
    keys = (
        ("OFF", "off"),
        ("DEF", "def"),
        ("PHY", "phy"),
        ("MEN", "men"),
    )
    out: list[dict[str, Any]] = []
    for label, key in keys:
        v = cat.get(key)
        out.append({"label": label, "value": int(round(v)) if v is not None else None, "pct": _rating_to_pct(v)})
    return out


def _goalie_skill_dna(cat: dict[str, float | None], form: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, key in (("GOA", "goa"), ("MEN", "men")):
        v = cat.get(key)
        out.append({"label": label, "value": int(round(v)) if v is not None else None, "pct": _rating_to_pct(v)})
    out.append({"label": "Form", "value": None, "pct": 70.0 if form.get("label") == "Hot" else 45.0 if form.get("label") == "Cold" else 60.0})
    return out


def _skater_usage(st: PlayerSkaterStat | None) -> list[dict[str, Any]]:
    if not st:
        return []
    fo_pct = None
    if (st.faceoffs or 0) > 0 and st.faceoff_wins is not None:
        fo_pct = round(100.0 * st.faceoff_wins / st.faceoffs, 1)
    return [
        {"label": "TOI/G", "value": _fmt_toi_per_game(st.toi_seconds, st.gp), "help": "Time on ice per game"},
        {
            "label": "PP TOI/G",
            "value": _fmt_toi_per_game(st.ppto_seconds, st.gp),
            "help": "Power-play time on ice per game",
        },
        {
            "label": "SH TOI/G",
            "value": _fmt_toi_per_game(st.shto_seconds, st.gp),
            "help": "Short-handed time on ice per game",
        },
        {"label": "FO%", "value": f"{fo_pct}%" if fo_pct is not None else "—", "help": "Faceoff win percentage"},
        {"label": "PDO", "value": f"{st.pdo:.1f}" if st.pdo is not None else "—", "help": "Shooting percentage plus save percentage while on ice"},
        {"label": "GR", "value": f"{st.game_rating:.0f}" if st.game_rating is not None else "—", "help": "Average game rating"},
    ]


def _goalie_crease_profile(
    st: PlayerGoalieStat | None,
    cat: dict[str, float | None],
    gr: float | None,
) -> list[dict[str, Any]]:
    gp = int(st.gp or 0) if st else 0
    gs_pct = None
    if st and gp > 0 and st.games_started is not None:
        gs_pct = round(100.0 * st.games_started / gp, 1)
    toi_g = _fmt_toi_per_game(st.minutes_played, gp) if st else None
    return [
        {"label": "GP", "value": str(gp) if gp else "—", "help": "Games played"},
        {"label": "GS%", "value": f"{gs_pct}%" if gs_pct is not None else "—", "help": "Percentage of appearances started"},
        {"label": "TOI/G", "value": toi_g or "—", "help": "Time on ice per game"},
        {"label": "SV%", "value": f"{st.sv_pct:.3f}" if st and st.sv_pct is not None else "—", "help": "Save percentage"},
        {"label": "GAA", "value": f"{st.gaa:.2f}" if st and st.gaa is not None else "—", "help": "Goals against average"},
        {"label": "SO", "value": str(st.so or 0) if st else "—", "help": "Shutouts"},
        {"label": "GOA", "value": int(round(cat.get("goa"))) if cat.get("goa") is not None else "—", "help": "Goalie ratings category average"},
        {
            "label": "GR",
            "value": f"{gr:.0f}" if gr is not None else (f"{st.game_rating:.0f}" if st and st.game_rating else "—"),
            "help": "Average game rating",
        },
    ]
