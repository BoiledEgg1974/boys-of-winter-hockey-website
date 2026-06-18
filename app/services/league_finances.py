"""League-wide player and staff finances for GM Finances page."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Config
from app.models import Player, PlayerContract, Season
from app.services.league_rules import rule_bool, rule_int
from app.services.seasons import get_current_season, season_display_label
from app.services.staff_salaries import (
    StaffDefaultSalaries,
    budgets_for_season,
    compute_staff_default_salaries,
    gm_display_name,
    main_league_teams,
)
from app.services.staff_transactions import active_roster_for_team
from app.site_models import GmLeagueMembership, TeamCapPenalty, User

SalaryGroup = Literal["forwards", "defense", "goalies"]


def _resolve_raw_import_dir(raw_import_dir: Path | None) -> Path:
    """Use the mounted league app's CSV folder, not the static ``Config`` default."""
    if raw_import_dir is not None:
        return raw_import_dir
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            configured = current_app.config.get("RAW_IMPORT_DIR")
            if configured:
                return Path(str(configured))
    except (RuntimeError, ImportError):
        pass
    return Path(Config.RAW_IMPORT_DIR)


def _norm_contract_key(key: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in key).strip("_")


def _read_semicolon_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f, delimiter=";"))
        except UnicodeDecodeError:
            continue
    return []


def _contract_rows_from_csv(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in _read_semicolon_rows(path):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
        pid = (nr.get("playerid") or "").strip()
        if pid:
            out[pid] = nr
    return out


def _parse_nonneg_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def merged_contract_rows(raw_import_dir: Path) -> dict[str, dict[str, str]]:
    """Load ``player_contract.csv`` and overlay ``player_contract_renewed.csv`` when present.

    Renewed exports often use a different starting year column (e.g. ``Major 2000`` without
    ``Major 1999``). Overlay field-by-field so base-year salaries are not dropped.
    """
    base = _contract_rows_from_csv(raw_import_dir / "player_contract.csv")
    renewed_path = raw_import_dir / "player_contract_renewed.csv"
    if renewed_path.is_file():
        for pid, row in _contract_rows_from_csv(renewed_path).items():
            if pid in base:
                base[pid] = {**base[pid], **row}
            else:
                base[pid] = row
    return base


def player_master_fhm_team_ids(raw_import_dir: Path) -> dict[str, str]:
    """Map FHM ``PlayerId`` to the player's current ``TeamId`` from ``player_master.csv``."""
    out: dict[str, str] = {}
    for row in _read_semicolon_rows(raw_import_dir / "player_master.csv"):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
        pid = (nr.get("playerid") or "").strip()
        team_id = (nr.get("teamid") or "").strip()
        if pid and team_id:
            out[pid] = team_id
    return out


def affiliate_fhm_team_ids_by_parent(raw_import_dir: Path) -> dict[str, set[str]]:
    """Map NHL ``TeamId`` to affiliated farm ``TeamId`` values from ``team_data.csv``."""
    out: dict[str, set[str]] = {}
    for row in _read_semicolon_rows(raw_import_dir / "team_data.csv"):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
        team_id = (nr.get("teamid") or "").strip()
        parent_raw = (nr.get("parent_team_1") or "").strip()
        if not team_id or not parent_raw or parent_raw == "-1":
            continue
        out.setdefault(parent_raw, set()).add(team_id)
    return out


def team_line_player_ids(raw_import_dir: Path, nhl_fhm_id: str) -> set[str]:
    """FHM player ids assigned to line slots in ``team_lines.csv`` for an NHL team."""
    out: set[str] = set()
    for row in _read_semicolon_rows(raw_import_dir / "team_lines.csv"):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items() if k is not None}
        team_id = (nr.get("teamid") or "").strip()
        if team_id != nhl_fhm_id:
            continue
        for key, value in nr.items():
            if key == "teamid":
                continue
            text = str(value).strip()
            if text.isdigit():
                out.add(text)
    return out


def player_rs_gp_map(raw_import_dir: Path) -> dict[str, int]:
    """Regular-season GP keyed by FHM ``PlayerId`` from skater/goalie RS stats exports."""
    out: dict[str, int] = {}
    for name in ("player_skater_stats_rs.csv", "player_goalie_stats_rs.csv"):
        for row in _read_semicolon_rows(raw_import_dir / name):
            nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
            pid = (nr.get("playerid") or "").strip()
            gp_raw = (nr.get("gp") or nr.get("games_played") or "").strip()
            if pid and gp_raw.isdigit():
                out[pid] = int(gp_raw)
    return out


def active_nhl_roster_player_ids(
    nhl_fhm_id: str,
    player_team_ids: dict[str, str],
    line_player_ids: set[str],
    *,
    player_gp: dict[str, int] | None = None,
) -> set[str]:
    """NHL roster ids that match the FHM finances CAP HIT column.

    ``team_lines.csv`` is complete for most clubs (within two of the NHL assignment
    count). When it is, use line assignments only so scratched extras are excluded.
    When lines lag, count line players plus scratched players who have played (GP > 0).
    Players with GP = 0 in the RS stats export are treated as non-roster for cap.
    """
    nhl_ids = {pid for pid, team_id in player_team_ids.items() if team_id == nhl_fhm_id}
    in_lines = {pid for pid in nhl_ids if pid in line_player_ids}
    if len(in_lines) >= len(nhl_ids) - 2:
        return in_lines
    if not player_gp:
        return nhl_ids
    scratch = nhl_ids - in_lines
    active_scratch = {
        pid for pid in scratch if player_gp.get(pid) is None or int(player_gp.get(pid, 0)) > 0
    }
    return in_lines | active_scratch


def uses_lines_only_roster(
    nhl_fhm_id: str,
    player_team_ids: dict[str, str],
    line_player_ids: set[str],
) -> bool:
    """True when FHM ``team_lines`` assignments are complete enough to drive cap hit."""
    nhl_ids = {pid for pid, team_id in player_team_ids.items() if team_id == nhl_fhm_id}
    in_lines = {pid for pid in nhl_ids if pid in line_player_ids}
    return len(in_lines) >= len(nhl_ids) - 2


def player_cap_hit(
    merged_row: dict[str, str] | None,
    base_row: dict[str, str] | None,
    year: int,
    *,
    contract: PlayerContract | None = None,
) -> int | None:
    """Cap hit shown on the FHM NHL roster screen for the current season year."""
    year_sal = contract_year_salary(merged_row, year)
    if year_sal is None:
        if contract is not None and contract.average_salary is not None:
            db_aav = int(contract.average_salary)
            if db_aav >= 0:
                return db_aav
        return None
    base_avg = _parse_nonneg_int(base_row.get("average_salary") if base_row else None)
    if base_avg is not None and base_avg > int(year_sal) and base_avg <= int(int(year_sal) * 1.15):
        return base_avg
    return int(year_sal)


def contract_salary_for_player(
    row: dict[str, str] | None,
    year: int,
    *,
    contract: PlayerContract | None = None,
) -> int | None:
    """Year salary from CSV, then CSV average, then DB contract AAV."""
    sal = contract_year_salary(row, year)
    if sal is not None:
        return sal
    if row:
        raw = (row.get("average_salary") or "").strip()
        if raw:
            try:
                v = int(float(raw))
                if v >= 0:
                    return v
            except (TypeError, ValueError):
                pass
    if contract is not None and contract.average_salary is not None:
        aav = int(contract.average_salary)
        if aav >= 0:
            return aav
    return None


def contract_year_val(row: dict[str, str] | None, prefix: str, year: int) -> int | None:
    if not row:
        return None
    raw = (row.get(f"{prefix}_{year}") or "").strip()
    if raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def contract_year_salary(row: dict[str, str] | None, year: int) -> int | None:
    """Prefer NHL (major) salary; fall back to minors when major is ``-1``."""
    major = contract_year_val(row, "major", year)
    if major is not None and major >= 0:
        return major
    minor = contract_year_val(row, "minor", year)
    if minor is not None and minor >= 0:
        return minor
    return None


def player_salary_group(position: str | None) -> SalaryGroup:
    pos = (position or "").upper()
    if pos in ("LW", "C", "RW"):
        return "forwards"
    if pos in ("D", "LD", "RD"):
        return "defense"
    if pos.startswith("G"):
        return "goalies"
    return "forwards"


def _pct_of_ceiling(amount: int, ceiling: int | None) -> float | None:
    if ceiling is None or ceiling <= 0:
        return None
    return 100.0 * float(amount) / float(ceiling)


def _season_label_for_start_year(start_year: int) -> str:
    end = start_year + 1
    return f"{start_year}-{end % 100:02d}"


def cap_penalties_for_season(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
) -> dict[int, int]:
    """Manual cap hit penalties keyed by league ``teams.id``."""
    rows = session.scalars(
        select(TeamCapPenalty).where(
            TeamCapPenalty.league_slug == league_slug,
            TeamCapPenalty.season_start_year == int(season_start_year),
        )
    ).all()
    return {int(r.team_id): int(r.penalty_amount) for r in rows}


def cap_penalty_admin_context(session: Session, *, league_slug: str) -> dict[str, object]:
    """Template context for Admin → Cap Hit Penalties."""
    from app.services.staff_salaries import resolve_staff_season

    season, start_year, season_label = resolve_staff_season(session)
    teams = main_league_teams(session)
    penalty_by_team: dict[int, int] = {}
    if start_year is not None:
        penalty_by_team = cap_penalties_for_season(
            session,
            league_slug=league_slug,
            season_start_year=int(start_year),
        )

    active_mems = session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    mem_by_team = {int(m.team_id): m for m in active_mems}
    user_ids = {int(m.user_id) for m in active_mems}
    users_by_id = (
        {int(u.id): u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )

    team_rows: list[dict[str, object]] = []
    total_penalties = 0
    for team in teams:
        tid = int(team.id)
        amount = int(penalty_by_team.get(tid, 0))
        total_penalties += amount
        mem = mem_by_team.get(tid)
        user = users_by_id.get(int(mem.user_id)) if mem else None
        fhm = getattr(team, "fhm_team_id", None)
        team_rows.append(
            {
                "team": team,
                "gm_label": gm_display_name(user),
                "team_id_label": str(fhm).strip() if fhm is not None and str(fhm).strip() else str(tid),
                "penalty_amount": amount,
            }
        )

    return {
        "league_slug": str(league_slug).strip(),
        "season": season,
        "season_label": season_label,
        "season_start_year": start_year,
        "team_rows": team_rows,
        "total_penalties": total_penalties,
    }


def _role_estimated_salary(role: str, defaults: StaffDefaultSalaries | None) -> int:
    if defaults is None:
        return 0
    role_s = str(role or "").strip()
    if role_s == "head_coach":
        return int(defaults.head_coach)
    if role_s == "assistant_coach":
        return int(defaults.assistant_coaches)
    if role_s == "scout":
        return int(defaults.scouts)
    if role_s == "trainer":
        return int(defaults.trainer)
    return 0


def build_staff_finances_rows(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int | None,
) -> tuple[list[dict[str, object]], bool]:
    """Per-team staff budget rollup for the Staff Finances tab."""
    teams = main_league_teams(session)
    if season_start_year is None:
        return [], False

    budget_by_team = budgets_for_season(
        session,
        league_slug=league_slug,
        season_start_year=int(season_start_year),
    )
    has_budgets = bool(budget_by_team)
    total_budget = sum(int(v) for v in budget_by_team.values())
    defaults = compute_staff_default_salaries(total_budget, len(teams))

    active_mems = session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    mem_by_team = {int(m.team_id): m for m in active_mems}
    user_ids = {int(m.user_id) for m in active_mems}
    users_by_id = (
        {int(u.id): u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )

    rows: list[dict[str, object]] = []
    for team in teams:
        tid = int(team.id)
        budget_amount = int(budget_by_team.get(tid, 0))
        roster = active_roster_for_team(
            session,
            league_slug=league_slug,
            team_id=tid,
            season_start_year=int(season_start_year),
        )
        estimated_payroll = sum(
            _role_estimated_salary(str(entry.role), defaults) for entry in roster
        )
        mem = mem_by_team.get(tid)
        user = users_by_id.get(int(mem.user_id)) if mem else None
        rows.append(
            {
                "team": team,
                "gm_label": gm_display_name(user),
                "budget_amount": budget_amount,
                "estimated_payroll": int(estimated_payroll),
                "budget_remaining": int(budget_amount - estimated_payroll),
            }
        )

    rows.sort(key=lambda r: (-int(r["budget_amount"]), str(getattr(r["team"], "name", ""))))
    return rows, has_budgets


def build_league_finances_context(
    session: Session,
    *,
    league_slug: str,
    raw_import_dir: Path | None = None,
) -> dict[str, object]:
    season = get_current_season()
    if season is None:
        season = session.scalar(
            select(Season).order_by(Season.start_year.desc().nulls_last(), Season.id.desc()).limit(1)
        )
    season_start_year = int(season.start_year) if season is not None and season.start_year is not None else None
    season_label = season_display_label(season)
    next_season_label = (
        _season_label_for_start_year(int(season_start_year) + 1)
        if season_start_year is not None
        else ""
    )

    cap_enabled = rule_bool(session, league_slug, "salary_cap_enabled", default=False)
    ceiling_raw = rule_int(session, league_slug, "salary_cap_amount", default=0)
    floor_raw = rule_int(session, league_slug, "salary_cap_floor", default=0)
    cap_ceiling = int(ceiling_raw) if ceiling_raw > 0 else None
    cap_floor = int(floor_raw) if floor_raw > 0 else None

    base_dir = _resolve_raw_import_dir(raw_import_dir)
    contract_rows = merged_contract_rows(base_dir)
    base_contract_rows = _contract_rows_from_csv(base_dir / "player_contract.csv")
    player_team_ids = player_master_fhm_team_ids(base_dir)
    player_gp = player_rs_gp_map(base_dir)
    line_ids_by_team = {
        str(team.fhm_team_id).strip(): team_line_player_ids(base_dir, str(team.fhm_team_id).strip())
        for team in main_league_teams(session)
        if team.fhm_team_id is not None
    }

    teams = main_league_teams(session)
    team_rows: list[dict[str, object]] = []
    penalties_by_team: dict[int, int] = {}
    if season_start_year is not None:
        penalties_by_team = cap_penalties_for_season(
            session,
            league_slug=league_slug,
            season_start_year=int(season_start_year),
        )

    for team in teams:
        if team.fhm_team_id is None:
            team_rows.append(
                _empty_team_finance_row(
                    team,
                    cap_ceiling=cap_ceiling,
                    cap_floor=cap_floor,
                    cap_enabled=cap_enabled,
                )
            )
            continue

        nhl_fhm_id = str(team.fhm_team_id).strip()
        line_ids = line_ids_by_team.get(nhl_fhm_id, set())
        active_roster_ids = active_nhl_roster_player_ids(
            nhl_fhm_id,
            player_team_ids,
            line_ids,
            player_gp=player_gp,
        )
        lines_only_roster = uses_lines_only_roster(nhl_fhm_id, player_team_ids, line_ids)

        contracts = session.scalars(
            select(PlayerContract)
            .options(joinedload(PlayerContract.player))
            .join(Player, Player.id == PlayerContract.player_id)
            .where(
                PlayerContract.fhm_team_id == team.fhm_team_id,
                Player.retired.is_(False),
            )
        ).unique().all()

        fwd_total = 0
        def_total = 0
        gk_total = 0
        roster_cap = 0
        next_year_cap = 0
        contract_count = 0

        if season_start_year is not None:
            cur_year = int(season_start_year)
            nxt_year = cur_year + 1
            for contract in contracts:
                player = contract.player
                if player is None:
                    continue
                pid = str(player.fhm_player_id or "").strip()
                crow = contract_rows.get(pid)
                base_row = base_contract_rows.get(pid)
                roster_team_id = player_team_ids.get(pid)
                on_nhl_roster = pid in active_roster_ids or (
                    roster_team_id is None and player.current_team_id == team.id
                )

                if on_nhl_roster:
                    cur_salary = player_cap_hit(crow, base_row, cur_year, contract=contract)
                    if cur_salary is None:
                        continue
                    contract_count += 1
                    roster_cap += int(cur_salary)
                    group = player_salary_group(player.position)
                    if group == "forwards":
                        fwd_total += int(cur_salary)
                    elif group == "defense":
                        def_total += int(cur_salary)
                    else:
                        gk_total += int(cur_salary)

                    nxt_salary = contract_year_salary(crow, nxt_year)
                    if nxt_salary is not None:
                        next_year_cap += int(nxt_salary)

        if lines_only_roster and cap_floor is not None and cap_floor > 0:
            roster_cap = max(int(roster_cap), int(cap_floor))

        cap_penalty = int(penalties_by_team.get(int(team.id), 0))
        total_cap = int(roster_cap) + cap_penalty
        pct_of_ceiling = _pct_of_ceiling(total_cap, cap_ceiling)
        cap_space = (int(cap_ceiling) - total_cap) if cap_ceiling is not None else None
        over_ceiling = bool(cap_enabled and cap_ceiling is not None and total_cap > cap_ceiling)
        under_floor = bool(cap_enabled and cap_floor is not None and total_cap < cap_floor)

        team_rows.append(
            {
                "team": team,
                "current_cap": total_cap,
                "roster_cap": int(roster_cap),
                "cap_penalty": cap_penalty,
                "contract_count": int(contract_count),
                "fwd_total": int(fwd_total),
                "def_total": int(def_total),
                "gk_total": int(gk_total),
                "fwd_pct_ceiling": _pct_of_ceiling(fwd_total, cap_ceiling),
                "def_pct_ceiling": _pct_of_ceiling(def_total, cap_ceiling),
                "gk_pct_ceiling": _pct_of_ceiling(gk_total, cap_ceiling),
                "next_year_cap": int(next_year_cap),
                "cap_space": cap_space,
                "pct_of_ceiling": pct_of_ceiling,
                "over_ceiling": over_ceiling,
                "under_floor": under_floor,
            }
        )

    team_rows.sort(key=lambda r: (-int(r["current_cap"]), str(getattr(r["team"], "name", ""))))

    staff_rows, staff_budgets_configured = build_staff_finances_rows(
        session,
        league_slug=league_slug,
        season_start_year=season_start_year,
    )

    return {
        "season": season,
        "season_label": season_label,
        "season_start_year": season_start_year,
        "next_season_label": next_season_label,
        "cap_enabled": cap_enabled,
        "cap_ceiling": cap_ceiling,
        "cap_floor": cap_floor,
        "teams": team_rows,
        "staff_rows": staff_rows,
        "staff_budgets_configured": staff_budgets_configured,
    }


def _empty_team_finance_row(
    team,
    *,
    cap_ceiling: int | None,
    cap_floor: int | None,
    cap_enabled: bool,
) -> dict[str, object]:
    return {
        "team": team,
        "current_cap": 0,
        "roster_cap": 0,
        "cap_penalty": 0,
        "contract_count": 0,
        "fwd_total": 0,
        "def_total": 0,
        "gk_total": 0,
        "fwd_pct_ceiling": None,
        "def_pct_ceiling": None,
        "gk_pct_ceiling": None,
        "next_year_cap": 0,
        "cap_space": int(cap_ceiling) if cap_ceiling is not None else None,
        "pct_of_ceiling": None,
        "over_ceiling": False,
        "under_floor": bool(cap_enabled and cap_floor is not None and 0 < cap_floor),
    }
