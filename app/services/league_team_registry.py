"""League team PK vs FHM franchise id registry and audits."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import league_by_slug, league_raw_import_dir, resolve_league_sqlite_path, resolve_site_sqlite_path


@dataclass(frozen=True)
class FhmTeamMasterRow:
    fhm_team_id: str
    name: str
    nickname: str
    abbreviation: str

    @property
    def display_name(self) -> str:
        nick = (self.nickname or "").strip()
        base = (self.name or "").strip()
        if not nick or nick.lower() in base.lower():
            return base
        return f"{base} {nick}".strip()


def _fhm_export_team_data_path(league_slug: str) -> Path | None:
    entry = league_by_slug(league_slug)
    if entry is None:
        return None
    path = Path(__file__).resolve().parents[2] / "data" / "imports" / "raw" / entry.raw_import_dir / "team_data.csv"
    return path if path.is_file() else None


def load_fhm_team_master(league_slug: str) -> list[FhmTeamMasterRow]:
    """Franchise rows from FHM ``team_data.csv`` (LeagueId 0)."""
    from app.services.import_validation import _read_csv_dict_rows

    path = _fhm_export_team_data_path(league_slug)
    if path is None:
        return []
    rows: list[FhmTeamMasterRow] = []
    for raw in _read_csv_dict_rows(path, delimiter=";"):
        league_id = str(raw.get("LeagueId") or raw.get("league_id") or "").strip()
        if league_id not in ("0", ""):
            continue
        fhm = str(raw.get("TeamId") or raw.get("team_id") or "").strip()
        abbr = str(raw.get("Abbr") or raw.get("abbreviation") or "").strip().upper()
        name = str(raw.get("Name") or raw.get("name") or "").strip()
        if not fhm or not abbr:
            continue
        rows.append(
            FhmTeamMasterRow(
                fhm_team_id=fhm,
                name=name,
                nickname=str(raw.get("Nickname") or raw.get("nickname") or "").strip(),
                abbreviation=abbr,
            )
        )
    return rows


def _league_teams_from_sqlite(league_slug: str) -> list[dict[str, Any]]:
    path = resolve_league_sqlite_path(league_slug)
    if not path.is_file():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(teams)").fetchall()}
        if "fhm_team_id" not in cols:
            return []
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, abbreviation, fhm_team_id, slug FROM teams ORDER BY id"
            ).fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def pk_fhm_collision_rows(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Teams whose PK equals another row's FHM franchise id (cross-id confusion risk)."""
    by_fhm: dict[str, dict[str, Any]] = {}
    for row in teams:
        fhm = str(row.get("fhm_team_id") or "").strip()
        if fhm:
            by_fhm[fhm] = row
    out: list[dict[str, Any]] = []
    for row in teams:
        pk = int(row["id"])
        other = by_fhm.get(str(pk))
        if other is None or int(other["id"]) == pk:
            continue
        out.append(
            {
                "team_pk": pk,
                "abbreviation": str(row.get("abbreviation") or ""),
                "name": str(row.get("name") or ""),
                "fhm_team_id": str(row.get("fhm_team_id") or ""),
                "other_pk": int(other["id"]),
                "other_abbrev": str(other.get("abbreviation") or ""),
                "other_name": str(other.get("name") or ""),
                "other_fhm_team_id": str(other.get("fhm_team_id") or ""),
            }
        )
    return out


def _membership_rows(league_slug: str) -> list[dict[str, Any]]:
    site_path = resolve_site_sqlite_path()
    if not site_path.is_file():
        return []
    uri = site_path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, user_id, team_id, fhm_team_id, status "
                "FROM gm_league_memberships WHERE league_slug = ?",
                (league_slug,),
            ).fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def audit_league_team_ids(league_slug: str) -> dict[str, Any]:
    slug = str(league_slug or "").strip()
    master = load_fhm_team_master(slug)
    master_by_fhm = {row.fhm_team_id: row for row in master}
    master_by_abbr = {row.abbreviation.upper(): row for row in master}
    teams = _league_teams_from_sqlite(slug)
    teams_by_pk = {int(t["id"]): t for t in teams}
    teams_by_fhm = {
        str(t.get("fhm_team_id") or "").strip(): t
        for t in teams
        if str(t.get("fhm_team_id") or "").strip()
    }

    missing_in_db: list[dict[str, str]] = []
    for row in master:
        if row.fhm_team_id not in teams_by_fhm:
            missing_in_db.append(
                {
                    "fhm_team_id": row.fhm_team_id,
                    "abbreviation": row.abbreviation,
                    "display_name": row.display_name,
                }
            )

    fhm_mismatches: list[dict[str, Any]] = []
    for team in teams:
        abbr = str(team.get("abbreviation") or "").strip().upper()
        expected = master_by_abbr.get(abbr)
        if expected is None:
            continue
        db_fhm = str(team.get("fhm_team_id") or "").strip()
        if db_fhm != expected.fhm_team_id:
            fhm_mismatches.append(
                {
                    "team_pk": int(team["id"]),
                    "abbreviation": abbr,
                    "db_fhm_team_id": db_fhm,
                    "expected_fhm_team_id": expected.fhm_team_id,
                }
            )

    membership_issues: list[dict[str, Any]] = []
    for mem in _membership_rows(slug):
        if str(mem.get("status") or "") != "active":
            continue
        pk = int(mem.get("team_id") or 0)
        team = teams_by_pk.get(pk)
        if team is None:
            membership_issues.append(
                {
                    "membership_id": int(mem["id"]),
                    "user_id": int(mem["user_id"]),
                    "team_pk": pk,
                    "issue": "team_pk not found in league teams table",
                }
            )
            continue
        expected_fhm = str(team.get("fhm_team_id") or "").strip()
        mem_fhm = str(mem.get("fhm_team_id") or "").strip()
        if expected_fhm and mem_fhm and mem_fhm != expected_fhm:
            membership_issues.append(
                {
                    "membership_id": int(mem["id"]),
                    "user_id": int(mem["user_id"]),
                    "team_pk": pk,
                    "issue": f"membership fhm_team_id={mem_fhm!r} but team row has {expected_fhm!r}",
                }
            )
        elif expected_fhm and not mem_fhm:
            membership_issues.append(
                {
                    "membership_id": int(mem["id"]),
                    "user_id": int(mem["user_id"]),
                    "team_pk": pk,
                    "issue": f"missing membership fhm_team_id (expected {expected_fhm!r})",
                }
            )

    pk_fhm_collisions = pk_fhm_collision_rows(teams)
    mapping = [
        {
            "team_pk": int(t["id"]),
            "abbreviation": str(t.get("abbreviation") or ""),
            "name": str(t.get("name") or ""),
            "fhm_team_id": str(t.get("fhm_team_id") or ""),
            "slug": str(t.get("slug") or ""),
            "master_display_name": (
                master_by_fhm.get(str(t.get("fhm_team_id") or "").strip()).display_name
                if str(t.get("fhm_team_id") or "").strip() in master_by_fhm
                else ""
            ),
        }
        for t in teams
    ]

    issues = bool(missing_in_db or fhm_mismatches or membership_issues)
    return {
        "league_slug": slug,
        "raw_import_dir": league_raw_import_dir(slug),
        "team_count": len(teams),
        "master_count": len(master),
        "teams": mapping,
        "missing_in_db": missing_in_db,
        "fhm_mismatches": fhm_mismatches,
        "pk_fhm_collision_warnings": pk_fhm_collisions,
        "membership_issues": membership_issues,
        "ok": not issues,
    }


def team_map_for_league(league_slug: str) -> dict[int, dict[str, str]]:
    """League PK -> {abbreviation, name, fhm_team_id, slug} from the league SQLite DB."""
    teams = _league_teams_from_sqlite(league_slug)
    return {
        int(t["id"]): {
            "abbreviation": str(t.get("abbreviation") or ""),
            "name": str(t.get("name") or ""),
            "fhm_team_id": str(t.get("fhm_team_id") or ""),
            "slug": str(t.get("slug") or ""),
        }
        for t in teams
    }


def write_league_team_map_json(league_slug: str, path: Path | None = None) -> Path:
    """Write canonical PK/FHM map for a league (defaults to raw import dir)."""
    slug = str(league_slug or "").strip()
    report = audit_league_team_ids(slug)
    if path is None:
        raw = league_raw_import_dir(slug)
        path = Path(__file__).resolve().parents[2] / "data" / "imports" / "raw" / raw / "team_pk_fhm_map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "league_slug": slug,
        "description": (
            "Site teams.id (PK) vs FHM franchise TeamId. "
            "Use PK for articles/memberships; use fhm_team_id for Discord emoji maps."
        ),
        "teams": report["teams"],
        "pk_fhm_collision_warnings": report["pk_fhm_collision_warnings"],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def repair_membership_fhm_ids(league_slug: str) -> int:
    """Set membership ``fhm_team_id`` from the league team row for one slug."""
    from sqlalchemy import select

    from app.league_db import db
    from app.services.register_team_options import fhm_team_id_for_league_team
    from app.site_models import GmLeagueMembership

    slug = str(league_slug or "").strip()
    mems = db.session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    changed = 0
    for mem in mems:
        expected = fhm_team_id_for_league_team(slug, int(mem.team_id))
        if not expected:
            continue
        current = str(mem.fhm_team_id or "").strip()
        if current != expected:
            mem.fhm_team_id = expected
            changed += 1
    if changed:
        db.session.commit()
    return changed
