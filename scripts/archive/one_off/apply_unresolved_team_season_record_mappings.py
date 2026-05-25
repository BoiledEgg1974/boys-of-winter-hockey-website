#!/usr/bin/env python3
"""Apply accepted Team ID suggestions back to team_season_records_template.csv.

Reads `<raw_dir>/team_season_records_unresolved_team_ids.csv` and, for each row with a
non-empty `Suggested Team ID`, updates matching rows in `team_season_records_template.csv`
by `(Year, Team Name Override)`.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data" / "imports" / "raw"
TARGET_NAME = "team_season_records_template.csv"
MAPPINGS_NAME = "team_season_records_unresolved_team_ids.csv"


def _key(year: str, name: str) -> tuple[str, str]:
    return (year or "").strip(), (name or "").strip()


def apply_mappings(raw_dir: Path, dry_run: bool = False) -> tuple[int, int, int]:
    target = raw_dir / TARGET_NAME
    mappings = raw_dir / MAPPINGS_NAME
    if not target.is_file() or not mappings.is_file():
        return 0, 0, 0

    with mappings.open(newline="", encoding="utf-8-sig") as f:
        map_rows = list(csv.DictReader(f))
    accepted: dict[tuple[str, str], str] = {}
    for r in map_rows:
        sid = (r.get("Suggested Team ID") or "").strip()
        if not sid:
            continue
        accepted[_key(r.get("Year", ""), r.get("Team Name Override", ""))] = sid

    with target.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0, 0, 0
    fieldnames = list(rows[0].keys())

    touched = updated = unchanged = 0
    for r in rows:
        k = _key(r.get("Year", ""), r.get("Team Name Override", ""))
        sid = accepted.get(k)
        if not sid:
            continue
        touched += 1
        cur = (r.get("Team ID") or "").strip()
        if cur == sid:
            unchanged += 1
            continue
        r["Team ID"] = sid
        updated += 1

    if not dry_run:
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
    return touched, updated, unchanged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--raw-dir",
        action="append",
        default=[],
        help="Raw import directory path (repeatable). Defaults to bowl_cap + bowl_historical.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Preview counts without writing files.")
    args = ap.parse_args()

    raw_dirs = [Path(p).resolve() for p in args.raw_dir] if args.raw_dir else [
        (RAW_ROOT / "bowl_cap").resolve(),
        (RAW_ROOT / "bowl_historical").resolve(),
    ]
    for raw in raw_dirs:
        touched, updated, unchanged = apply_mappings(raw, dry_run=args.dry_run)
        mode = "dry-run" if args.dry_run else "applied"
        print(
            f"{raw.name}: {mode} mappings touched={touched} updated={updated} unchanged={unchanged}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
