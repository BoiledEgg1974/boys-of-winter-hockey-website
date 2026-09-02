"""Peer-market contract value / cap efficiency for team pages.

Model value is the median current-year AAV among league players with a similar
composite overall at the same position group. That is a market read of what
similar-rated players are paid — not a proprietary projection or xG model.
"""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, PlayerContract, Season, Team
from app.services.league_finances import (
    merged_contract_rows,
    player_cap_hit,
    player_salary_group,
)
from app.services.league_rules import rule_bool
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.staff_salaries import main_league_teams

MIN_MARKET_AAV = 1_000
PEER_WINDOW = 4
MIN_PEERS = 5
MAX_WINDOW = 18

GRADE_BANDS: tuple[tuple[float, str], ...] = (
    (140.0, "A+"),
    (125.0, "A"),
    (115.0, "A-"),
    (105.0, "B+"),
    (95.0, "B"),
    (88.0, "B-"),
    (80.0, "C+"),
    (72.0, "C"),
    (64.0, "C-"),
    (52.0, "D"),
    (0.0, "D-"),
)

_snapshot_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def compact_money(amount: int | float | None) -> str:
    if amount is None:
        return "—"
    n = float(amount)
    sign = "-" if n < 0 else ""
    mag = abs(n)
    if mag >= 1_000_000:
        val = mag / 1_000_000.0
        body = f"{val:.1f}".rstrip("0").rstrip(".") + "M"
        return f"{sign}${body}"
    if mag >= 1_000:
        val = mag / 1_000.0
        body = f"{val:.1f}".rstrip("0").rstrip(".") + "k"
        return f"{sign}${body}"
    return f"{sign}${int(round(mag)):,}"


def signed_compact_money(amount: int | float | None) -> str:
    if amount is None:
        return "—"
    label = compact_money(amount)
    if float(amount) > 0:
        return "+" + label
    return label


def english_ordinal(n: int) -> str:
    n = int(n)
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    m = n % 10
    if m == 1:
        return f"{n}st"
    if m == 2:
        return f"{n}nd"
    if m == 3:
        return f"{n}rd"
    return f"{n}th"


def value_grade(value_pct: float | None) -> str | None:
    if value_pct is None:
        return None
    for threshold, grade in GRADE_BANDS:
        if float(value_pct) >= threshold:
            return grade
    return "D-"


def value_band(value_pct: float | None) -> str:
    if value_pct is None:
        return "na"
    if float(value_pct) >= 115.0:
        return "bargain"
    if float(value_pct) <= 80.0:
        return "overpay"
    return "fair"


def grade_tone(grade: str | None) -> str:
    if not grade:
        return "muted"
    letter = grade[:1]
    if letter == "A":
        return "good"
    if letter == "B":
        return "ok"
    if letter == "C":
        return "mid"
    return "bad"


def peer_median_aav(
    overall: int,
    samples: list[tuple[int, int]],
    *,
    window: int = PEER_WINDOW,
    min_peers: int = MIN_PEERS,
) -> tuple[int | None, int]:
    """Return (median AAV, peer count) among samples near ``overall``."""
    if not samples or overall <= 0:
        return None, 0
    w = max(0, int(window))
    while w <= MAX_WINDOW:
        peers = [aav for ovr, aav in samples if abs(int(ovr) - int(overall)) <= w]
        if len(peers) >= min_peers or w == MAX_WINDOW:
            if not peers:
                return None, 0
            return int(round(median(peers))), len(peers)
        w += 2
    return None, 0


def _group_key(position: str | None) -> str:
    g = player_salary_group(position)
    if g == "defense":
        return "defense"
    if g == "goalies":
        return "goalies"
    return "forwards"


def _display_group(position: str | None, is_minor: bool) -> str:
    if is_minor:
        return "minors"
    return _group_key(position)


def league_uses_salary_cap(session: Session, league_slug: str) -> bool:
    """True when the admin cap flag is on, or this is the named BOWL-Cap league."""
    if rule_bool(session, league_slug, "salary_cap_enabled", default=False):
        return True
    return str(league_slug or "").strip() == "bowl-cap"


def years_remaining_from_row(row: dict[str, Any] | None, start_year: int | None) -> int:
    if not row or start_year is None:
        return 0
    y = int(start_year)
    n = 0
    while y < 2100:
        raw = row.get(f"major_{y}")
        if raw is None or str(raw).strip() == "":
            break
        try:
            v = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            break
        if v < 0:
            break
        n += 1
        y += 1
    return n


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return 0.0


def _cache_token(league_slug: str, season: Season | None, raw_dir: Path) -> tuple[Any, ...]:
    start = int(season.start_year) if season and season.start_year is not None else 0
    return (
        league_slug,
        start,
        _mtime(raw_dir / "player_contract.csv"),
        _mtime(raw_dir / "player_contract_renewed.csv"),
        _mtime(raw_dir / "player_ratings.csv"),
    )


def _player_overall(player: Player) -> int | None:
    rr = get_player_ratings_row(player.fhm_player_id)
    return compute_player_overall_100(
        player.overall_ability,
        player.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(player),
    )


def _build_snapshot(
    session: Session,
    *,
    league_slug: str,
    season: Season | None,
    raw_dir: Path,
) -> dict[str, Any]:
    start_year = int(season.start_year) if season and season.start_year is not None else None
    merged = merged_contract_rows(raw_dir)
    main_teams = main_league_teams(session)
    by_fhm: dict[str, Team] = {}
    for team in main_teams:
        if team.fhm_team_id is None:
            continue
        by_fhm[str(team.fhm_team_id).strip()] = team

    contracts = session.scalars(
        select(PlayerContract).options(
            joinedload(PlayerContract.player).joinedload(Player.current_team)
        )
    ).unique().all()

    samples: dict[str, list[tuple[int, int]]] = {
        "forwards": [],
        "defense": [],
        "goalies": [],
        "all": [],
    }
    staged: list[dict[str, Any]] = []

    for contract in contracts:
        player = contract.player
        if player is None or contract.fhm_team_id is None:
            continue
        nhl_team = by_fhm.get(str(contract.fhm_team_id).strip())
        if nhl_team is None:
            continue
        fhm_id = str(player.fhm_player_id or "").strip()
        csv_row = merged.get(fhm_id) if fhm_id else None
        if start_year is None:
            continue
        aav = player_cap_hit(csv_row, csv_row, start_year, contract=contract)
        if aav is None:
            continue
        years = years_remaining_from_row(csv_row, start_year)
        overall = _player_overall(player)
        pos_group = _group_key(player.position)
        is_minor = bool(player.current_team_id and player.current_team_id != nhl_team.id)
        in_market = int(aav) >= MIN_MARKET_AAV and overall is not None
        if in_market and overall is not None:
            samples[pos_group].append((int(overall), int(aav)))
            samples["all"].append((int(overall), int(aav)))
        flags: list[str] = []
        if contract.has_nmc:
            flags.append("NMC")
        if contract.has_ntc:
            flags.append("NTC")
        if contract.is_elc:
            flags.append("ELC")
        staged.append(
            {
                "player_id": int(player.id),
                "player_name": player.full_name,
                "pos": player_positions_display_label(player),
                "pos_group": pos_group,
                "display_group": _display_group(player.position, is_minor),
                "team_id": int(nhl_team.id),
                "aav": int(aav),
                "years_left": int(years),
                "overall": overall,
                "in_market": in_market,
                "is_ufa": bool(contract.is_ufa),
                "flags": flags,
            }
        )

    def model_for(row: dict[str, Any]) -> tuple[int | None, int]:
        if not row["in_market"] or row["overall"] is None:
            return None, 0
        pool = samples.get(str(row["pos_group"])) or []
        if len(pool) < MIN_PEERS:
            pool = samples["all"]
        return peer_median_aav(int(row["overall"]), pool)

    players_out: list[dict[str, Any]] = []
    team_acc: dict[int, dict[str, float]] = {
        int(t.id): {"model": 0.0, "aav": 0.0, "surplus_year": 0.0, "surplus_term": 0.0, "n": 0}
        for t in main_teams
    }

    for row in staged:
        model, peers = model_for(row)
        aav = int(row["aav"])
        years = max(int(row["years_left"] or 0), 1) if model is not None else int(row["years_left"] or 0)
        value_pct = round(100.0 * float(model) / float(aav), 1) if model and aav else None
        surplus_year = int(model - aav) if model is not None else None
        surplus_term = int(surplus_year * max(int(row["years_left"] or 0), 1)) if surplus_year is not None else None
        grade = value_grade(value_pct)
        band = value_band(value_pct)
        if model is not None:
            acc = team_acc.setdefault(
                int(row["team_id"]),
                {"model": 0.0, "aav": 0.0, "surplus_year": 0.0, "surplus_term": 0.0, "n": 0},
            )
            acc["model"] += float(model)
            acc["aav"] += float(aav)
            acc["surplus_year"] += float(surplus_year or 0)
            acc["surplus_term"] += float(surplus_term or 0)
            acc["n"] += 1
        players_out.append(
            {
                **row,
                "model_value": model,
                "peer_count": peers,
                "value_pct": value_pct,
                "surplus_year": surplus_year,
                "surplus_term": surplus_term,
                "grade": grade,
                "grade_tone": grade_tone(grade),
                "band": band,
                "aav_label": compact_money(aav),
                "model_label": compact_money(model),
                "surplus_year_label": signed_compact_money(surplus_year),
                "surplus_term_label": signed_compact_money(surplus_term),
                "contract_label": (
                    f"{compact_money(aav)} × {int(row['years_left'])} yr"
                    if row["years_left"]
                    else compact_money(aav)
                ),
                "years": years,
                "value_bar_pct": round(min(float(value_pct), 160.0) / 1.6, 1) if value_pct is not None else 0,
            }
        )

    def _rank(metric: str) -> dict[int, int]:
        ordered = sorted(
            team_acc.items(),
            key=lambda item: (-float(item[1][metric]), int(item[0])),
        )
        return {tid: idx + 1 for idx, (tid, _) in enumerate(ordered)}

    rank_year = _rank("surplus_year")
    rank_term = _rank("surplus_term")
    league_n = len(team_acc)

    teams_out: dict[int, dict[str, Any]] = {}
    for tid, acc in team_acc.items():
        avg_pct = round(100.0 * acc["model"] / acc["aav"], 1) if acc["aav"] else None
        grade = value_grade(avg_pct)
        teams_out[tid] = {
            "team_id": tid,
            "avg_value_pct": avg_pct,
            "surplus_year": int(acc["surplus_year"]),
            "surplus_term": int(acc["surplus_term"]),
            "market_n": int(acc["n"]),
            "grade": grade,
            "grade_tone": grade_tone(grade),
            "rank": rank_term.get(tid),
            "rank_year": rank_year.get(tid),
            "rank_term": rank_term.get(tid),
            "league_n": league_n,
        }

    cap_enabled = league_uses_salary_cap(session, league_slug)
    return {
        "cap_enabled": cap_enabled,
        "title": "Cap Efficiency" if cap_enabled else "Contract Value",
        "players": players_out,
        "teams": teams_out,
        "league_n": league_n,
        "season_start_year": start_year,
    }


def league_contract_value_snapshot(
    session: Session,
    *,
    league_slug: str,
    season: Season | None,
    raw_import_dir: Path,
) -> dict[str, Any]:
    raw_dir = Path(raw_import_dir)
    token = _cache_token(league_slug, season, raw_dir)
    hit = _snapshot_cache.get(league_slug)
    if hit is not None and hit[0] == token:
        return hit[1]
    snap = _build_snapshot(session, league_slug=league_slug, season=season, raw_dir=raw_dir)
    _snapshot_cache[league_slug] = (token, snap)
    return snap


def build_team_finances_payload(
    session: Session,
    team: Team,
    *,
    season: Season | None,
    league_slug: str,
    raw_import_dir: Path,
) -> dict[str, Any]:
    snap = league_contract_value_snapshot(
        session,
        league_slug=league_slug,
        season=season,
        raw_import_dir=raw_import_dir,
    )
    team_id = int(team.id)
    players = [p for p in snap["players"] if int(p["team_id"]) == team_id]
    players.sort(
        key=lambda r: (
            {"forwards": 0, "defense": 1, "goalies": 2, "minors": 3}.get(str(r["display_group"]), 9),
            -(r["value_pct"] if r["value_pct"] is not None else -1),
            str(r["player_name"]),
        )
    )
    summary = snap["teams"].get(team_id) or {
        "avg_value_pct": None,
        "surplus_year": 0,
        "surplus_term": 0,
        "market_n": 0,
        "grade": None,
        "grade_tone": "muted",
        "rank": None,
        "rank_year": None,
        "rank_term": None,
        "league_n": snap["league_n"],
    }
    bargains = sum(1 for p in players if p["band"] == "bargain")
    overpays = sum(1 for p in players if p["band"] == "overpay")
    fair = sum(1 for p in players if p["band"] == "fair")
    league_n = int(summary.get("league_n") or 0)

    def _rank_label(rank_key: str) -> str | None:
        rank = summary.get(rank_key)
        if rank and league_n:
            return f"{english_ordinal(int(rank))} of {league_n}"
        return None

    rank_label = _rank_label("rank_year") or _rank_label("rank")
    return {
        "cap_enabled": bool(snap["cap_enabled"]),
        "title": snap["title"],
        "note": (
            "Peer-market read of current-year AAV versus similarly rated players at the same "
            "position. Composite overall uses FHM ability, potential, and attribute grades — "
            "not expected goals."
        ),
        "players": players,
        "summary": {
            **summary,
            "avg_value_pct_label": (
                f"{summary['avg_value_pct']:.0f}%" if summary.get("avg_value_pct") is not None else "—"
            ),
            "surplus_year_label": signed_compact_money(summary.get("surplus_year")),
            "surplus_term_label": signed_compact_money(summary.get("surplus_term")),
            "rank_label": rank_label,
            "rank_year_label": _rank_label("rank_year"),
            "rank_term_label": _rank_label("rank_term"),
            "bargains": bargains,
            "overpays": overpays,
            "fair": fair,
        },
        "counts": {
            "all": len(players),
            "forwards": sum(1 for p in players if p["display_group"] == "forwards"),
            "defense": sum(1 for p in players if p["display_group"] == "defense"),
            "goalies": sum(1 for p in players if p["display_group"] == "goalies"),
            "minors": sum(1 for p in players if p["display_group"] == "minors"),
        },
    }
