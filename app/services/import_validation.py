from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.models import Team


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_csv_rows_autodelim(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        return list(csv.DictReader(fh, delimiter=delim))


def collect_team_identity_history_logo_issues(*, raw_dir: Path, static_root: Path) -> list[str]:
    """Return human-readable lines for each row in ``team_identity_history.csv`` with a missing ``logo_file``."""
    p = raw_dir / "team_identity_history.csv"
    if not p.is_file():
        return []
    rows = _read_csv_rows_autodelim(p)
    issues: list[str] = []
    static_root = static_root.resolve()
    for i, row in enumerate(rows, start=2):
        logo = _first_present_key(row, ("logo_file", "logo_file_override"))
        if not logo:
            tid = _first_present_key(row, ("team_fhm_id", "team_id")) or "?"
            y0 = _first_present_key(row, ("start_year", "year_start", "year")) or "?"
            y1 = _first_present_key(row, ("end_year", "year_end")) or y0
            nm = _first_present_key(row, ("team_name", "display_name")) or ""
            issues.append(
                f"Row {i}: empty logo_file (team_fhm_id={tid}, years={y0}-{y1}, name={nm!r})"
            )
            continue
        rel = logo.strip().lstrip("/\\").replace("\\", "/")
        if not rel.startswith("logos/"):
            rel = f"logos/teams/{raw_dir.name}/{rel}"
        full = (static_root / rel).resolve()
        if not full.is_file():
            tid = _first_present_key(row, ("team_fhm_id", "team_id")) or "?"
            y0 = _first_present_key(row, ("start_year", "year_start", "year")) or "?"
            y1 = _first_present_key(row, ("end_year", "year_end")) or y0
            nm = _first_present_key(row, ("team_name", "display_name")) or ""
            issues.append(
                f"Row {i}: missing file for logo_file={logo!r} -> expected {full} "
                f"(team_fhm_id={tid}, years={y0}-{y1}, name={nm!r})"
            )
    return issues


def _first_present_key(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    lowered = {str(k).strip().lower(): k for k in row.keys()}
    for key in keys:
        src = lowered.get(key)
        if src is None:
            continue
        val = (row.get(src) or "").strip()
        if val:
            return val
    return None


def build_import_validation_report(*, raw_dir: Path, team_logos_dir: Path, session) -> dict:
    required_files = ("player_master.csv", "team_master.csv", "games.csv")
    optional_files = (
        "player_stats.csv",
        "goalie_stats.csv",
        "team_stats.csv",
        "history_awards.sheet.csv",
        "history_awards.csv",
        "history_all_stars.csv",
        "team_identity_history.csv",
    )
    errors: list[str] = []
    warnings: list[str] = []
    details: list[str] = []

    if not raw_dir.is_dir():
        errors.append(f"Raw import folder not found: {raw_dir}")
        return {
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "raw_dir": str(raw_dir),
            "team_logos_dir": str(team_logos_dir),
            "static_root": "",
            "errors": errors,
            "warnings": warnings,
            "details": details,
            "csv_counts": {},
            "missing_required": list(required_files),
            "missing_logos": [],
            "duplicate_player_ids": [],
            "duplicate_team_ids": [],
            "team_identity_history_issues": [],
        }

    csv_counts: dict[str, int] = {}
    missing_required = [name for name in required_files if not (raw_dir / name).is_file()]
    if missing_required:
        errors.append("Missing required CSV files.")
        for name in missing_required:
            details.append(f"Missing required file: {name}")

    for name in required_files + optional_files:
        p = raw_dir / name
        if not p.is_file():
            continue
        rows = _read_csv_rows(p)
        csv_counts[name] = len(rows)

    player_dupes: list[str] = []
    player_rows = _read_csv_rows(raw_dir / "player_master.csv")
    if player_rows:
        seen = Counter()
        for r in player_rows:
            pid = _first_present_key(r, ("playerid", "player_id"))
            if pid:
                seen[pid] += 1
        player_dupes = sorted([pid for pid, n in seen.items() if n > 1])
        if player_dupes:
            errors.append("Duplicate player IDs found in player_master.csv.")

    team_dupes: list[str] = []
    team_rows = _read_csv_rows(raw_dir / "team_master.csv")
    if team_rows:
        seen = Counter()
        for r in team_rows:
            tid = _first_present_key(r, ("teamid", "team_id"))
            if tid:
                seen[tid] += 1
        team_dupes = sorted([tid for tid, n in seen.items() if n > 1])
        if team_dupes:
            errors.append("Duplicate team IDs found in team_master.csv.")

    missing_logos: list[str] = []
    teams = session.scalars(select(Team)).all()
    for t in teams:
        slug = str(getattr(t, "slug", "") or "").strip()
        if not slug:
            continue
        has_logo = any((team_logos_dir / f"{slug}.{ext}").is_file() for ext in ("png", "webp", "jpg", "svg"))
        if not has_logo:
            missing_logos.append(slug)
    if missing_logos:
        warnings.append("Missing team logo files for one or more team slugs.")

    static_root = team_logos_dir.resolve().parent.parent.parent
    team_identity_history_issues = collect_team_identity_history_logo_issues(
        raw_dir=raw_dir, static_root=static_root
    )
    if team_identity_history_issues:
        warnings.append(
            "team_identity_history.csv references one or more missing or empty logo_file entries."
        )

    if not errors and not warnings:
        details.append("No issues found in current read-only validation checks.")

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "raw_dir": str(raw_dir),
        "team_logos_dir": str(team_logos_dir),
        "static_root": str(static_root),
        "errors": errors,
        "warnings": warnings,
        "details": details,
        "csv_counts": csv_counts,
        "missing_required": missing_required,
        "missing_logos": missing_logos,
        "duplicate_player_ids": player_dupes,
        "duplicate_team_ids": team_dupes,
        "team_identity_history_issues": team_identity_history_issues,
    }
