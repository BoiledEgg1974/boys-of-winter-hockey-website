"""Promote current game-record leaders into persistent baseline rows.

Run once after deploy for every BOWL league (Historical, Relegation, Cap):

    python scripts/sync_game_record_baselines.py --all

Or one mount at a time:

    python scripts/sync_game_record_baselines.py bowl-historical
    python scripts/sync_game_record_baselines.py bowl-fantasy
    python scripts/sync_game_record_baselines.py bowl-cap

Per-league Flask CLI (with LEAGUE_SLUG set): ``flask bowl-game-record-baselines-sync``
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
from app.services.game_records import sync_game_record_baselines
from app.sqlite_retry import commit_with_sqlite_retry


def _sync_league(slug: str) -> int:
    app = create_app(make_league_config(slug))
    with app.app_context():
        promoted = sync_game_record_baselines(db.session)
        commit_with_sqlite_retry(db.session)
        print(f"{slug}: promoted {promoted} baseline(s)")
        return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "league",
        nargs="?",
        help="League slug (e.g. bowl-historical). Omit when using --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync bowl-historical, bowl-fantasy, and bowl-cap.",
    )
    args = parser.parse_args()
    if args.all:
        total = 0
        for entry in LEAGUES:
            total += _sync_league(entry.slug)
        print(f"Total promoted: {total}")
        return 0
    slug = (args.league or "").strip()
    if not slug:
        parser.error("Provide a league slug or use --all.")
    _sync_league(slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
