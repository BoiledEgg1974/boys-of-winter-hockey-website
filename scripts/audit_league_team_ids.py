"""Audit league team PK vs FHM franchise id alignment and GM membership rows.

Example:
  python scripts/audit_league_team_ids.py bowl-cap
  python scripts/audit_league_team_ids.py --all --write-map
  python scripts/audit_league_team_ids.py bowl-cap --json
  python scripts/audit_league_team_ids.py bowl-cap --fix-memberships
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import league_slugs
from app.services.league_team_registry import (
    audit_league_team_ids,
    repair_membership_fhm_ids,
    write_league_team_map_json,
)


def _print_report(report: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    print(f"League: {report['league_slug']}")
    print(f"Teams in DB: {report['team_count']}")
    print(f"FHM master rows: {report['master_count']}")
    if report["missing_in_db"]:
        print("\nIn FHM master but missing from league DB:")
        for row in report["missing_in_db"]:
            print(f"  fhm {row['fhm_team_id']:>3} {row['abbreviation']} {row['display_name']}")
    if report["fhm_mismatches"]:
        print("\nFHM id mismatch (DB vs team_data.csv):")
        for row in report["fhm_mismatches"]:
            print(
                f"  pk {row['team_pk']:>2} {row['abbreviation']}: "
                f"db fhm={row['db_fhm_team_id']!r} expected={row['expected_fhm_team_id']!r}"
            )
    warnings = report.get("pk_fhm_collision_warnings") or []
    if warnings:
        print("\nPK / FHM collision warnings (expected when PK != FHM id; code must prefer PK):")
        for row in warnings:
            print(
                f"  pk {row['team_pk']:>2} {row['abbreviation']} ({row['name']}) "
                f"<-> fhm id of {row['other_abbrev']} ({row['other_name']}) pk {row['other_pk']}"
            )
    if report["membership_issues"]:
        print("\nGM membership issues:")
        for row in report["membership_issues"]:
            print(f"  user {row['user_id']} team_pk {row['team_pk']}: {row['issue']}")
    if report["ok"]:
        print("\nAll checked team and membership ids align.")
        return 0
    print("\nIssues found — review rows above.")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("league", nargs="?", help="League slug, e.g. bowl-cap")
    ap.add_argument("--all", action="store_true", help="Audit every configured league")
    ap.add_argument("--json", action="store_true", help="Print full report as JSON")
    ap.add_argument(
        "--fix-memberships",
        action="store_true",
        help="Backfill gm_league_memberships.fhm_team_id from league team rows",
    )
    ap.add_argument(
        "--write-map",
        action="store_true",
        help="Write data/imports/raw/<league>/team_pk_fhm_map.json from current DB",
    )
    args = ap.parse_args()
    if args.all:
        slugs = league_slugs()
    else:
        slug = str(args.league or "").strip()
        if not slug:
            ap.error("league slug required (or pass --all)")
        slugs = [slug]

    exit_code = 0
    for i, slug in enumerate(slugs):
        if args.fix_memberships:
            from app import create_app
            from app.config import make_league_config

            app = create_app(make_league_config(slug))
            with app.app_context():
                fixed = repair_membership_fhm_ids(slug)
            print(f"Updated {fixed} membership row(s) for {slug}.")
        if args.write_map:
            out = write_league_team_map_json(slug)
            print(f"Wrote {out}")
        report = audit_league_team_ids(slug)
        if args.all and not args.json and i:
            print()
        code = _print_report(report, as_json=args.json and not args.all)
        if code:
            exit_code = code
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
