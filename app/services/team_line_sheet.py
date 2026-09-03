"""GM-saved even-strength line builder sheets (site DB; not FHM write-back)."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context, has_request_context, url_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_login import ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, has_admin_role
from app.models import Player, PlayerContract, Prospect, Season, Team
from app.services.free_agents import player_ids_from_player_rights_csv_for_team
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_line_roles import (
    ROLES_BY_KEY,
    default_role_key,
    line_ability,
    line_ability_grade,
    line_chemistry,
    player_role_group,
    role_rating_for_key,
    role_scores_for_player,
)
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.trade_tool import gm_user_id_for_team
from app.site_models import TeamLineSheet

# Even strength, PP 5v4, and PK 4v5. Same player may occupy one slot per situation.
SLOT_DEFS: tuple[tuple[str, str, str, str, str], ...] = (
    ("es_l1_lw", "ES L1 LW", "LW", "es_l1_f", "forwards"),
    ("es_l1_c", "ES L1 C", "C", "es_l1_f", "forwards"),
    ("es_l1_rw", "ES L1 RW", "RW", "es_l1_f", "forwards"),
    ("es_l2_lw", "ES L2 LW", "LW", "es_l2_f", "forwards"),
    ("es_l2_c", "ES L2 C", "C", "es_l2_f", "forwards"),
    ("es_l2_rw", "ES L2 RW", "RW", "es_l2_f", "forwards"),
    ("es_l3_lw", "ES L3 LW", "LW", "es_l3_f", "forwards"),
    ("es_l3_c", "ES L3 C", "C", "es_l3_f", "forwards"),
    ("es_l3_rw", "ES L3 RW", "RW", "es_l3_f", "forwards"),
    ("es_l4_lw", "ES L4 LW", "LW", "es_l4_f", "forwards"),
    ("es_l4_c", "ES L4 C", "C", "es_l4_f", "forwards"),
    ("es_l4_rw", "ES L4 RW", "RW", "es_l4_f", "forwards"),
    ("es_l1_ld", "ES L1 LD", "LD", "es_l1_d", "defense"),
    ("es_l1_rd", "ES L1 RD", "RD", "es_l1_d", "defense"),
    ("es_l2_ld", "ES L2 LD", "LD", "es_l2_d", "defense"),
    ("es_l2_rd", "ES L2 RD", "RD", "es_l2_d", "defense"),
    ("es_l3_ld", "ES L3 LD", "LD", "es_l3_d", "defense"),
    ("es_l3_rd", "ES L3 RD", "RD", "es_l3_d", "defense"),
    ("es_l4_ld", "ES L4 LD", "LD", "es_l4_d", "defense"),
    ("es_l4_rd", "ES L4 RD", "RD", "es_l4_d", "defense"),
    ("pp_l1_lw", "PP5on4 L1 LW", "LW", "pp_l1", "forwards"),
    ("pp_l1_c", "PP5on4 L1 C", "C", "pp_l1", "forwards"),
    ("pp_l1_rw", "PP5on4 L1 RW", "RW", "pp_l1", "forwards"),
    ("pp_l1_ld", "PP5on4 L1 LD", "LD", "pp_l1", "defense"),
    ("pp_l1_rd", "PP5on4 L1 RD", "RD", "pp_l1", "defense"),
    ("pp_l2_lw", "PP5on4 L2 LW", "LW", "pp_l2", "forwards"),
    ("pp_l2_c", "PP5on4 L2 C", "C", "pp_l2", "forwards"),
    ("pp_l2_rw", "PP5on4 L2 RW", "RW", "pp_l2", "forwards"),
    ("pp_l2_ld", "PP5on4 L2 LD", "LD", "pp_l2", "defense"),
    ("pp_l2_rd", "PP5on4 L2 RD", "RD", "pp_l2", "defense"),
    ("pk_l1_f1", "PK4on5 L1 F1", "F1", "pk_l1", "forwards"),
    ("pk_l1_f2", "PK4on5 L1 F2", "F2", "pk_l1", "forwards"),
    ("pk_l1_ld", "PK4on5 L1 LD", "LD", "pk_l1", "defense"),
    ("pk_l1_rd", "PK4on5 L1 RD", "RD", "pk_l1", "defense"),
    ("pk_l2_f1", "PK4on5 L2 F1", "F1", "pk_l2", "forwards"),
    ("pk_l2_f2", "PK4on5 L2 F2", "F2", "pk_l2", "forwards"),
    ("pk_l2_ld", "PK4on5 L2 LD", "LD", "pk_l2", "defense"),
    ("pk_l2_rd", "PK4on5 L2 RD", "RD", "pk_l2", "defense"),
    ("pk_l3_f1", "PK4on5 L3 F1", "F1", "pk_l3", "forwards"),
    ("pk_l3_f2", "PK4on5 L3 F2", "F2", "pk_l3", "forwards"),
    ("pk_l3_ld", "PK4on5 L3 LD", "LD", "pk_l3", "defense"),
    ("pk_l3_rd", "PK4on5 L3 RD", "RD", "pk_l3", "defense"),
)

SLOT_KEYS: frozenset[str] = frozenset(k for k, *_ in SLOT_DEFS)
SLOT_GROUP: dict[str, str] = {k: g for k, _csv, _lab, _unit, g in SLOT_DEFS}


def slot_situation(slot_key: str) -> str:
    key = (slot_key or "").strip()
    if key.startswith("pp_"):
        return "pp"
    if key.startswith("pk_"):
        return "pk"
    return "es"


LINE_UNITS: tuple[dict[str, Any], ...] = (
    {"id": "es_l1_f", "title": "1st Line", "kind": "forwards", "slots": ("es_l1_lw", "es_l1_c", "es_l1_rw")},
    {"id": "es_l2_f", "title": "2nd Line", "kind": "forwards", "slots": ("es_l2_lw", "es_l2_c", "es_l2_rw")},
    {"id": "es_l3_f", "title": "3rd Line", "kind": "forwards", "slots": ("es_l3_lw", "es_l3_c", "es_l3_rw")},
    {"id": "es_l4_f", "title": "4th Line", "kind": "forwards", "slots": ("es_l4_lw", "es_l4_c", "es_l4_rw")},
    {"id": "es_l1_d", "title": "1st Pair", "kind": "defense", "slots": ("es_l1_ld", "es_l1_rd")},
    {"id": "es_l2_d", "title": "2nd Pair", "kind": "defense", "slots": ("es_l2_ld", "es_l2_rd")},
    {"id": "es_l3_d", "title": "3rd Pair", "kind": "defense", "slots": ("es_l3_ld", "es_l3_rd")},
    {"id": "es_l4_d", "title": "4th Pair", "kind": "defense", "slots": ("es_l4_ld", "es_l4_rd")},
    {"id": "pp_l1", "title": "Unit 1 (5v4)", "kind": "powerplay", "slots": ("pp_l1_lw", "pp_l1_c", "pp_l1_rw", "pp_l1_ld", "pp_l1_rd")},
    {"id": "pp_l2", "title": "Unit 2 (5v4)", "kind": "powerplay", "slots": ("pp_l2_lw", "pp_l2_c", "pp_l2_rw", "pp_l2_ld", "pp_l2_rd")},
    {"id": "pk_l1", "title": "Unit 1 (4v5)", "kind": "penalty", "slots": ("pk_l1_f1", "pk_l1_f2", "pk_l1_ld", "pk_l1_rd")},
    {"id": "pk_l2", "title": "Unit 2 (4v5)", "kind": "penalty", "slots": ("pk_l2_f1", "pk_l2_f2", "pk_l2_ld", "pk_l2_rd")},
    {"id": "pk_l3", "title": "Unit 3 (4v5)", "kind": "penalty", "slots": ("pk_l3_f1", "pk_l3_f2", "pk_l3_ld", "pk_l3_rd")},
)


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


def _norm_contract_key(key: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in key).strip("_")


def _contract_year_val(row: dict[str, str] | None, prefix: str, year: int) -> int | None:
    if not row:
        return None
    raw = (row.get(f"{prefix}_{year}") or "").strip()
    if raw == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def can_edit_line_sheet(user: Any, league_slug: str, team_id: int, session: Session) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if has_admin_role(user, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER):
        return True
    gm_id = gm_user_id_for_team(session, league_slug, int(team_id))
    return gm_id is not None and int(gm_id) == int(user.id)


def season_start_year_for_sheet(season: Season | None) -> int:
    if season is not None and season.start_year is not None:
        return int(season.start_year)
    return 0


def org_players_for_team(
    session: Session,
    team: Team,
    season: Season | None,
    raw_import_dir: Path,
) -> dict[int, Player]:
    """Roster + contracted org + prospects + rights — same stale-assignment guard as imported lines."""
    org: dict[int, Player] = {}
    roster = session.scalars(select(Player).where(Player.current_team_id == team.id)).all()
    for p in roster:
        org[int(p.id)] = p

    contract_rows: dict[str, dict[str, str]] = {}
    path = raw_import_dir / "player_contract.csv"
    for row in _read_semicolon_rows(path):
        nr = {_norm_contract_key(k): (v or "") for k, v in row.items()}
        pid = (nr.get("playerid") or "").strip()
        if pid:
            contract_rows[pid] = nr
    year = int(season.start_year) if season and season.start_year is not None else None

    def _has_current_contract(pl: Player) -> bool:
        if pl.current_team_id is not None:
            return True
        if year is None:
            return False
        crow = contract_rows.get(str(pl.fhm_player_id or "").strip())
        major_v = _contract_year_val(crow, "major", year)
        minor_v = _contract_year_val(crow, "minor", year)
        return bool(
            (major_v is not None and major_v >= 0)
            or (minor_v is not None and minor_v >= 0)
        )

    if team.fhm_team_id is not None:
        contracted = session.scalars(
            select(Player)
            .join(PlayerContract, PlayerContract.player_id == Player.id)
            .where(PlayerContract.fhm_team_id == team.fhm_team_id)
        ).all()
        for p in contracted:
            if _has_current_contract(p) or p.current_team_id == team.id:
                org[int(p.id)] = p
            else:
                org[int(p.id)] = p

    prospects = session.scalars(
        select(Player).join(Prospect, Prospect.player_id == Player.id).where(Prospect.team_id == team.id)
    ).all()
    for p in prospects:
        org[int(p.id)] = p

    for pid in player_ids_from_player_rights_csv_for_team(session, raw_import_dir, team):
        pl = session.get(Player, int(pid))
        if pl is None or bool(getattr(pl, "retired", False)):
            continue
        org[int(pl.id)] = pl
    return org


def imported_slot_map(
    team: Team,
    org: dict[int, Player],
    raw_import_dir: Path,
    session: Session,
) -> dict[str, int]:
    lines_path = raw_import_dir / "team_lines.csv"
    lines_row: dict[str, str] = {}
    team_fhm = str(team.fhm_team_id) if team.fhm_team_id is not None else None
    if team_fhm:
        for row in _read_semicolon_rows(lines_path):
            if (row.get("TeamId") or row.get("teamid") or "").strip() == team_fhm:
                lines_row = row
                break
    fhm_to_player: dict[str, Player] = {}
    for p in org.values():
        if p.fhm_player_id is not None and str(p.fhm_player_id).strip():
            fhm_to_player[str(p.fhm_player_id).strip()] = p
    extra_ids = sorted(
        {
            str(v).strip()
            for v in lines_row.values()
            if v is not None and str(v).strip().isdigit()
        }
    )
    if extra_ids:
        extras = session.scalars(select(Player).where(Player.fhm_player_id.in_(extra_ids))).all()
        for p in extras:
            if p.fhm_player_id is not None:
                fhm_to_player[str(p.fhm_player_id).strip()] = p

    allowed = set(org.keys())
    out: dict[str, int] = {}
    used_by_sit: dict[str, set[int]] = {"es": set(), "pp": set(), "pk": set()}
    for slot_key, csv_col, *_rest in SLOT_DEFS:
        raw = (lines_row.get(csv_col) or "").strip()
        if not raw:
            continue
        pl = fhm_to_player.get(raw)
        if pl is None or int(pl.id) not in allowed:
            continue
        if pl.current_team_id is not None and int(pl.current_team_id) != int(team.id):
            continue
        pid = int(pl.id)
        sit = slot_situation(slot_key)
        if pid in used_by_sit[sit]:
            continue
        used_by_sit[sit].add(pid)
        out[slot_key] = pid
    return out


def load_sheet(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    season_start_year: int,
) -> TeamLineSheet | None:
    return session.scalar(
        select(TeamLineSheet).where(
            TeamLineSheet.league_slug == league_slug,
            TeamLineSheet.team_id == int(team_id),
            TeamLineSheet.season_start_year == int(season_start_year),
        ).limit(1)
    )


def sanitize_slots(
    raw_slots: Any,
    org_ids: set[int],
    players_by_id: dict[int, Player] | None = None,
) -> tuple[dict[str, int], str | None]:
    if raw_slots is None:
        return {}, None
    if not isinstance(raw_slots, dict):
        return {}, "slots must be an object"
    out: dict[str, int] = {}
    used_by_sit: dict[str, set[int]] = {"es": set(), "pp": set(), "pk": set()}
    for key, val in raw_slots.items():
        slot = str(key).strip()
        if slot not in SLOT_KEYS:
            return {}, f"unknown slot {slot}"
        if val is None or val == "":
            continue
        try:
            pid = int(val)
        except (TypeError, ValueError):
            return {}, f"invalid player for {slot}"
        if pid not in org_ids:
            return {}, "player is not on this organization"
        sit = slot_situation(slot)
        if pid in used_by_sit[sit]:
            return {}, "player cannot occupy two slots"
        if players_by_id is not None:
            pl = players_by_id.get(pid)
            rr = get_player_ratings_row(getattr(pl, "fhm_player_id", None) if pl else None)
            group = player_role_group(getattr(pl, "position", None) if pl else None, rr)
            if group != SLOT_GROUP[slot]:
                return {}, f"player does not match {slot} position group"
        used_by_sit[sit].add(pid)
        out[slot] = pid
    return out, None


def sanitize_roles(raw_roles: Any, org_ids: set[int]) -> tuple[dict[str, str], str | None]:
    if raw_roles is None:
        return {}, None
    if not isinstance(raw_roles, dict):
        return {}, "roles must be an object"
    out: dict[str, str] = {}
    for key, val in raw_roles.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        if pid not in org_ids:
            return {}, "player is not on this organization"
        role = str(val or "").strip()
        if not role:
            continue
        if role not in ROLES_BY_KEY:
            return {}, f"unknown role {role}"
        out[str(pid)] = role
    return out, None


def save_sheet(
    session: Session,
    *,
    league_slug: str,
    team: Team,
    season: Season | None,
    raw_import_dir: Path,
    slots: dict[str, int],
    roles: dict[str, str],
    user_id: int,
) -> TeamLineSheet:
    year = season_start_year_for_sheet(season)
    row = load_sheet(
        session,
        league_slug=league_slug,
        team_id=int(team.id),
        season_start_year=year,
    )
    now = datetime.utcnow()
    if row is None:
        row = TeamLineSheet(
            league_slug=league_slug,
            team_id=int(team.id),
            season_start_year=year,
            slots_json="{}",
            roles_json="{}",
            updated_by_user_id=int(user_id),
            updated_at=now,
        )
        session.add(row)
    row.slots_json = json.dumps({k: int(v) for k, v in slots.items()})
    row.roles_json = json.dumps({str(k): str(v) for k, v in roles.items()})
    row.updated_by_user_id = int(user_id)
    row.updated_at = now
    return row


def _player_payload(pl: Player) -> dict[str, Any]:
    rr = get_player_ratings_row(pl.fhm_player_id)
    is_g = player_is_goalie_for_overall(pl)
    ovr = compute_player_overall_100(pl.overall_ability, pl.overall_potential, rr, is_goalie=is_g)
    scores = role_scores_for_player(rr, position=pl.position)
    group = player_role_group(pl.position, rr)
    href = ""
    headshot = ""
    if has_request_context():
        href = url_for("main.player_page", player_id=int(pl.id))
        if has_app_context():
            static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
            rel = resolve_player_headshot_static_filename(
                static_root,
                pl,
                str(current_app.config.get("PLAYER_HEADSHOTS_REL_DIR") or "players"),
            )
            if rel:
                headshot = url_for("static", filename=rel)
    hand = (pl.shoots_catches or "").strip()
    return {
        "id": int(pl.id),
        "name": pl.full_name or "",
        "pos": player_positions_display_label(pl),
        "group": group,
        "ovr": ovr,
        "hand": hand[:1].upper() if hand else "",
        "headshot": headshot,
        "href": href,
        "roles": scores,
        "default_role": default_role_key(scores),
    }


def _roles_for_slots(
    slots: dict[str, int],
    saved_roles: dict[str, str],
    players: dict[int, dict[str, Any]],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for pid in slots.values():
        rec = players.get(int(pid))
        if not rec:
            continue
        wanted = saved_roles.get(str(pid))
        valid_keys = {r["key"] for r in rec.get("roles") or []}
        if wanted and wanted in valid_keys:
            out[str(pid)] = str(wanted)
        else:
            default = rec.get("default_role")
            if default:
                out[str(pid)] = str(default)
    # Keep extra saved role prefs for unused pool players
    for key, role in saved_roles.items():
        if str(key) in out:
            continue
        rec = players.get(int(key)) if str(key).isdigit() else None
        if not rec:
            continue
        valid_keys = {r["key"] for r in rec.get("roles") or []}
        if role in valid_keys:
            out[str(key)] = role
    return out


def _unit_stats(
    unit: dict[str, Any],
    slots: dict[str, int],
    roles: dict[str, str],
    players: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    ratings: list[int | None] = []
    role_keys: list[str | None] = []
    hands: list[str | None] = []
    for slot in unit["slots"]:
        pid = slots.get(slot)
        if not pid:
            ratings.append(None)
            role_keys.append(None)
            hands.append(None)
            continue
        rec = players.get(int(pid)) or {}
        role = roles.get(str(pid))
        rating = role_rating_for_key(list(rec.get("roles") or []), role)
        ratings.append(rating)
        role_keys.append(role)
        hands.append(rec.get("hand"))
    ability = line_ability(ratings)
    grade = line_ability_grade(ability, kind=str(unit.get("kind") or ""), role_keys=role_keys)
    return {
        "ability": ability,
        "chemistry": line_chemistry(role_keys, hands),
        "grade": grade,
    }


def build_line_builder_payload(
    session: Session,
    *,
    team: Team,
    season: Season | None,
    league_slug: str,
    raw_import_dir: Path,
    viewer: Any,
) -> dict[str, Any]:
    org = org_players_for_team(session, team, season, raw_import_dir)
    imported = imported_slot_map(team, org, raw_import_dir, session)
    year = season_start_year_for_sheet(season)
    sheet = load_sheet(
        session,
        league_slug=league_slug,
        team_id=int(team.id),
        season_start_year=year,
    )
    players = {int(p.id): _player_payload(p) for p in org.values()}
    imported_roles = _roles_for_slots(imported, {}, players)
    if sheet is not None:
        saved_slots, _ = sanitize_slots(sheet.slots_map(), set(org.keys()), players_by_id=org)
        saved_roles, _ = sanitize_roles(sheet.roles_map(), set(org.keys()))
        slots = saved_slots
        roles = _roles_for_slots(slots, saved_roles, players)
        source = "saved"
        updated_at = sheet.updated_at.isoformat(timespec="seconds") if sheet.updated_at else None
    else:
        slots = dict(imported)
        roles = dict(imported_roles)
        source = "imported"
        updated_at = None

    units = []
    for unit in LINE_UNITS:
        stats = _unit_stats(unit, slots, roles, players)
        units.append(
            {
                "id": unit["id"],
                "title": unit["title"],
                "kind": unit["kind"],
                "slots": [
                    {
                        "key": sk,
                        "label": next(lab for k, _c, lab, _u, _g in SLOT_DEFS if k == sk),
                        "group": SLOT_GROUP[sk],
                        "situation": slot_situation(sk),
                        "player_id": slots.get(sk),
                    }
                    for sk in unit["slots"]
                ],
                "ability": stats["ability"],
                "chemistry": stats["chemistry"],
                "grade": stats["grade"],
            }
        )

    save_url = ""
    if has_request_context():
        save_url = url_for("api.save_team_line_sheet", slug=team.slug)

    pool = sorted(
        players.values(),
        key=lambda r: (
            {"forwards": 0, "defense": 1, "goalies": 2}.get(str(r.get("group")), 9),
            -(int(r["ovr"]) if r.get("ovr") is not None else -1),
            str(r.get("name") or ""),
        ),
    )
    return {
        "team_slug": team.slug,
        "save_url": save_url,
        "can_save": can_edit_line_sheet(viewer, league_slug, int(team.id), session),
        "source": source,
        "updated_at": updated_at,
        "season_start_year": year,
        "imported_slots": imported,
        "imported_roles": imported_roles,
        "slots": slots,
        "roles": roles,
        "players": players,
        "pool": pool,
        "units": units,
        "slot_defs": [
            {"key": k, "label": lab, "group": g, "unit": unit}
            for k, _csv, lab, unit, g in SLOT_DEFS
        ],
    }
