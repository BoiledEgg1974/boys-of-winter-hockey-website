"""Queue Discord boxscores for recent final games (franchise channels).

Uses the same helper as Admin → Discord Integration → Queue recent boxscores.

  python scripts/queue_recent_game_boxscores.py --all
  python scripts/queue_recent_game_boxscores.py --league bowl-cap --days 7
  python scripts/queue_recent_game_boxscores.py --all --dry-run
  python scripts/queue_recent_game_boxscores.py --league bowl-historical --days 7 --force
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _target_slugs(args: argparse.Namespace) -> list[str]:
    from app.config import league_slugs

    if args.all:
        return list(league_slugs())
    if args.league:
        return [str(args.league).strip()]
    raise SystemExit("Pass --all or --league <slug>.")


def _queue_league(slug: str, *, days: int, dry_run: bool, force: bool) -> dict:
    from app import create_app
    from app.config import make_league_config
    from app.league_db import db
    from app.services.game_boxscore_discord import (
        queue_recent_game_boxscores,
        recent_final_game_ids_for_boxscores,
    )
    from app.sqlite_retry import commit_with_sqlite_retry

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    with app.app_context():
        if dry_run:
            game_ids, start, latest = recent_final_game_ids_for_boxscores(
                db.session, days=days
            )
            print(
                f"{slug}: dry-run window {start} → {latest}; "
                f"{len(game_ids)} final game(s): {game_ids}"
                + (" (force)" if force else "")
            )
            return {
                "games": len(game_ids),
                "queued": 0,
                "skipped": 0,
                "ok": True,
            }
        stats = queue_recent_game_boxscores(
            db.session,
            db.session,
            league_slug=slug,
            days=days,
            force=force,
        )
        commit_with_sqlite_retry(db.session)
        print(f"{slug}: {stats.get('message') or stats}")
        return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="All configured league mounts.")
    g.add_argument("--league", help="Single league slug (e.g. bowl-cap).")
    ap.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of in-game calendar days ending at the latest final (default 7).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching games without enqueueing.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-queue already-sent games (clears delivery locks; posts new Discord messages).",
    )
    args = ap.parse_args()
    if args.days < 1:
        raise SystemExit("--days must be >= 1")

    totals = {"games": 0, "queued": 0, "skipped": 0}
    for slug in _target_slugs(args):
        stats = _queue_league(
            slug,
            days=int(args.days),
            dry_run=bool(args.dry_run),
            force=bool(args.force),
        )
        for k in totals:
            totals[k] += int(stats.get(k) or 0)
    print(f"done: {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
