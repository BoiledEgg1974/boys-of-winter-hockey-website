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
            "errors": errors,
            "warnings": warnings,
            "details": details,
            "csv_counts": {},
            "missing_required": list(required_files),
            "missing_logos": [],
            "duplicate_player_ids": [],
            "duplicate_team_ids": [],
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

    if not errors and not warnings:
        details.append("No issues found in current read-only validation checks.")

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "raw_dir": str(raw_dir),
        "team_logos_dir": str(team_logos_dir),
        "errors": errors,
        "warnings": warnings,
        "details": details,
        "csv_counts": csv_counts,
        "missing_required": missing_required,
        "missing_logos": missing_logos,
        "duplicate_player_ids": player_dupes,
        "duplicate_team_ids": team_dupes,
    }
