"""Exclude site user ids from BOWL Six for one or more leagues (shared site DB).

Examples::

  python scripts/bowl_six_exclude_users.py --user-id 8 --league bowl-historical --league bowl-cap
  python scripts/bowl_six_exclude_users.py --user-id 8 --all-hockey --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

HOCKEY_LEAGUES = ("bowl-historical", "bowl-cap", "bowl-fantasy")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exclude users from BOWL Six participation.")
    parser.add_argument(
        "--user-id",
        type=int,
        action="append",
        dest="user_ids",
        required=True,
        help="Site user id to exclude (repeatable).",
    )
    parser.add_argument(
        "--league",
        action="append",
        dest="leagues",
        help="League slug (repeatable).",
    )
    parser.add_argument(
        "--all-hockey",
        action="store_true",
        help="Apply to bowl-historical, bowl-cap, and bowl-fantasy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing.",
    )
    parser.add_argument(
        "--keep-lineups",
        action="store_true",
        help="Do not delete open/locked slate lineups for excluded users.",
    )
    args = parser.parse_args()

    leagues = list(args.leagues or [])
    if args.all_hockey:
        leagues.extend(HOCKEY_LEAGUES)
    leagues = sorted({str(s).strip() for s in leagues if str(s).strip()})
    if not leagues:
        parser.error("Pass --league and/or --all-hockey.")

    user_ids = {int(u) for u in args.user_ids}

    from app import create_app
    from app.services.bowl_six import bowl_six_excluded_user_ids, exclude_users_from_bowl_six

    app = create_app()
    with app.app_context():
        from app.league_db import db

        for slug in leagues:
            before = bowl_six_excluded_user_ids(db.session, slug)
            if args.dry_run:
                after = before | user_ids
                print(
                    f"{slug}: would set bowl_six_excluded_user_ids="
                    f"{','.join(str(i) for i in sorted(after))}"
                )
                continue
            result = exclude_users_from_bowl_six(
                db.session,
                slug,
                user_ids,
                remove_open_lineups=not args.keep_lineups,
            )
            after = bowl_six_excluded_user_ids(db.session, slug)
            print(
                f"{slug}: excluded={','.join(str(i) for i in sorted(after))} "
                f"lineups_removed={result['lineups_removed']}"
            )


if __name__ == "__main__":
    main()
