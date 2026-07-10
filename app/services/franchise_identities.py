"""Editable historical identities for relocated/renamed franchises."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import current_app, has_request_context, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import FranchiseTeamIdentity, Team


@dataclass(frozen=True)
class TeamIdentityView:
    display_name: str
    abbreviation: str | None = None
    logo_file: str | None = None
    status: str = "historical"
    identity_id: int | None = None


@dataclass(frozen=True)
class FranchiseIdentitySeedResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0


def identity_logo_static_rel(raw: str | None) -> str | None:
    rel = str(raw or "").strip().lstrip("/\\").replace("\\", "/")
    if not rel:
        return None
    if rel.startswith("static/"):
        rel = rel[len("static/") :]
    if not rel.startswith("logos/"):
        team_logos_rel = str(current_app.config.get("TEAM_LOGOS_REL_DIR") or "logos/teams")
        rel = f"{team_logos_rel.rstrip('/')}/{rel}"
    return rel


def identity_logo_url(raw: str | None) -> str | None:
    rel = identity_logo_static_rel(raw)
    if not rel:
        return None
    static_path = Path(current_app.static_folder or "") / rel
    if static_path.is_file():
        return url_for("static", filename=rel)
    return None


def _seed_end_year(raw: str | None) -> int | None:
    try:
        value = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return None if value >= 2100 else value


def norm_fhm_team_id(raw: object) -> str | None:
    """Normalize FHM team ids; preserves ``0`` (Montreal Canadiens)."""
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s != "" else None


def _csv_identity_row_count(csv_path: Path) -> int:
    if not csv_path.is_file():
        return 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def franchise_identities_need_csv_seed(session: Session, csv_path: Path) -> bool:
    """True when the league DB is empty or the CSV has rows not yet upserted."""
    if not csv_path.is_file():
        return False
    db_count = int(session.scalar(select(func.count()).select_from(FranchiseTeamIdentity)) or 0)
    if db_count == 0:
        return True
    return _csv_identity_row_count(csv_path) > db_count


def sync_franchise_identities_from_csv_if_needed(
    session: Session, csv_path: Path
) -> FranchiseIdentitySeedResult | None:
    """Upsert franchise identity rows when the CSV has grown or the table is empty."""
    if not franchise_identities_need_csv_seed(session, csv_path):
        return None
    return seed_franchise_identities_from_csv(session, csv_path)


def seed_franchise_identities_from_csv(session: Session, csv_path: Path) -> FranchiseIdentitySeedResult:
    """Upsert editable identity rows from the legacy team_identity_history.csv file."""
    path = Path(csv_path)
    if not path.is_file():
        return FranchiseIdentitySeedResult(skipped=1)

    created = updated = skipped = 0
    teams_by_fhm: dict[str, Team] = {}
    for t in session.scalars(select(Team).where(Team.fhm_team_id.is_not(None))).all():
        fhm = norm_fhm_team_id(t.fhm_team_id)
        if fhm is not None:
            teams_by_fhm[fhm] = t
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            fhm = norm_fhm_team_id(row.get("team_fhm_id"))
            name = " ".join(str(row.get("team_name") or "").strip().split())
            try:
                start_year = int(str(row.get("start_year") or "").strip())
            except (TypeError, ValueError):
                skipped += 1
                continue
            if fhm is None or not name:
                skipped += 1
                continue
            end_year = _seed_end_year(row.get("end_year"))
            team = teams_by_fhm.get(fhm)
            existing = session.scalar(
                select(FranchiseTeamIdentity)
                .where(
                    FranchiseTeamIdentity.team_fhm_id == fhm,
                    FranchiseTeamIdentity.display_name == name,
                    FranchiseTeamIdentity.start_year == start_year,
                    FranchiseTeamIdentity.end_year.is_(end_year)
                    if end_year is None
                    else FranchiseTeamIdentity.end_year == end_year,
                )
                .limit(1)
            )
            target = existing or FranchiseTeamIdentity()
            target.team_id = int(team.id) if team else target.team_id
            target.team_fhm_id = fhm
            target.display_name = name
            target.logo_file = str(row.get("logo_file") or "").strip() or None
            target.start_year = start_year
            target.end_year = end_year
            target.status = "defunct" if end_year is not None else "historical"
            if not target.notes:
                target.notes = f"Seeded from {path.name}"
            session.add(target)
            if existing is None:
                created += 1
            else:
                updated += 1
    return FranchiseIdentitySeedResult(created=created, updated=updated, skipped=skipped)


def team_identity_for_season(
    session: Session,
    team: Team | None = None,
    season_year: int | None = None,
    *,
    team_fhm_id: str | int | None = None,
) -> TeamIdentityView | None:
    """Return the historical identity that applies to ``team``/FHM id in a season."""
    if season_year is None:
        return None
    try:
        sy = int(season_year)
    except (TypeError, ValueError):
        return None

    tid = int(team.id) if team is not None and getattr(team, "id", None) is not None else None
    fhm = str(team_fhm_id if team_fhm_id is not None else getattr(team, "fhm_team_id", "") or "").strip()
    predicates = []
    if tid is not None:
        predicates.append(FranchiseTeamIdentity.team_id == tid)
    if fhm:
        predicates.append(FranchiseTeamIdentity.team_fhm_id == fhm)
    if not predicates:
        return None

    row = session.scalar(
        select(FranchiseTeamIdentity)
        .where(
            or_(*predicates),
            FranchiseTeamIdentity.start_year <= sy,
            or_(FranchiseTeamIdentity.end_year.is_(None), FranchiseTeamIdentity.end_year >= sy),
        )
        .order_by(
            FranchiseTeamIdentity.team_id.is_(None).asc(),
            FranchiseTeamIdentity.start_year.desc(),
            FranchiseTeamIdentity.id.desc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    return TeamIdentityView(
        display_name=(row.display_name or "").strip(),
        abbreviation=(row.abbreviation or "").strip() or None,
        logo_file=(row.logo_file or "").strip() or None,
        status=(row.status or "historical").strip() or "historical",
        identity_id=int(row.id),
    )


def _season_year_from_record(record: Any) -> int | None:
    sy = (
        getattr(record, "season_year", None)
        or getattr(record, "start_year", None)
        or getattr(record, "draft_year", None)
    )
    if isinstance(record, dict):
        sy = record.get("season_year") or record.get("start_year") or record.get("draft_year")
    try:
        return int(sy) if sy is not None else None
    except (TypeError, ValueError):
        return None


def identity_for_record(session: Session, record: Any) -> TeamIdentityView | None:
    """Resolve an identity for record-like objects used by templates/helpers."""
    if has_request_context() and (request.args.get("identity_view") or "").strip().lower() == "franchise":
        return None
    team = getattr(record, "team", None)
    if isinstance(record, dict):
        team = record.get("team")
    fhm = (
        getattr(record, "team_fhm_id_csv", None)
        or getattr(record, "team_fhm_id", None)
        or getattr(team, "fhm_team_id", None)
    )
    if isinstance(record, dict):
        fhm = record.get("team_fhm_id_csv") or record.get("team_fhm_id") or fhm
    sy = _season_year_from_record(record)
    if sy is None:
        return None
    return team_identity_for_season(session, team, sy, team_fhm_id=fhm)
