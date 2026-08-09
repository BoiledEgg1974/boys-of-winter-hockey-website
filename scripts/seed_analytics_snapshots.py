"""Seed analytics snapshots from current live season tables (one-time baseline).

Usage:
  python scripts/seed_analytics_snapshots.py --all
  python scripts/seed_analytics_snapshots.py bowl-historical
  python scripts/seed_analytics_snapshots.py bowl-fantasy --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import LEAGUES, make_league_config
from app.league_db import db
from app.services.analytics_snapshots import (
    record_analytics_snapshots_for_league,
    seed_analytics_snapshots_if_empty,
)
from app.services.seasons import get_current_season


def _seed_league(slug: str, *, force: bool) -> None:
    app = create_app(make_league_config(slug))
    with app.app_context():
        raw_dir = Path(app.config["RAW_IMPORT_DIR"])
        season = get_current_season()
        if force:
            counts = record_analytics_snapshots_for_league(
                db.session,
                slug,
                raw_dir=raw_dir,
                season=season,
                is_rollover=False,
            )
            print(f"{slug}: forced snapshot players={counts['players']} teams={counts['teams']} hubs={counts.get('hubs', 0)}")
            return
        counts = seed_analytics_snapshots_if_empty(
            db.session, slug, raw_dir=raw_dir, season=season
        )
        if counts["players"] or counts["teams"] or counts.get("hubs"):
            print(f"{slug}: seeded players={counts['players']} teams={counts['teams']} hubs={counts.get('hubs', 0)}")
        else:
            print(f"{slug}: already has analytics snapshots (use --force to append)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed player/team analytics snapshots")
    parser.add_argument(
        "leagues",
        nargs="*",
        help="League slug(s). Use --all for every configured league.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Seed every configured league.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Append a snapshot even when history already exists.",
    )
    args = parser.parse_args()
    slugs = [e.slug for e in LEAGUES] if args.all else list(args.leagues)
    if not slugs:
        parser.error("Pass one or more league slugs, or --all")
    for slug in slugs:
        _seed_league(slug, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
