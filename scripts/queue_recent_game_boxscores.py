"""Queue Discord boxscores for recent final games (franchise channels).

Uses the same helper as Admin → Discord Integration → Queue recent boxscores.

  python scripts/queue_recent_game_boxscores.py --all
  python scripts/queue_recent_game_boxscores.py --league bowl-cap --days 7
  python scripts/queue_recent_game_boxscores.py --all --dry-run
  python scripts/queue_recent_game_boxscores.py --league bowl-historical --days 7 --force
  python scripts/queue_recent_game_boxscores.py --league bowl-fantasy --game-type "Prospect Tournament" --force
  python scripts/queue_recent_game_boxscores.py --league bowl-fantasy --from-date 2025-10-05 --to-date 2025-10-08 --force
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
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


def _parse_iso_date(raw: str | None, flag: str) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise SystemExit(f"{flag} must be YYYY-MM-DD (got {text!r}).") from exc


def _queue_league(
    slug: str,
    *,
    days: int,
    game_type: str | None,
    from_date: date | None,
    to_date: date | None,
    dry_run: bool,
    force: bool,
) -> dict:
    from app import create_app
    from app.config import make_league_config
    from app.league_db import db
    from app.services.game_boxscore_discord import (
        final_game_ids_for_boxscore_queue,
        queue_recent_game_boxscores,
    )
    from app.sqlite_retry import commit_with_sqlite_retry

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    with app.app_context():
        if dry_run:
            game_ids, start, latest = final_game_ids_for_boxscore_queue(
                db.session,
                days=days,
                game_type=game_type,
                from_date=from_date,
                to_date=to_date,
            )
            if game_type:
                scope = f"type={game_type!r}"
            elif from_date is not None and to_date is not None:
                scope = f"dates={from_date.isoformat()}..{to_date.isoformat()}"
            else:
                scope = f"days={days}"
            print(
                f"{slug}: dry-run ({scope}) {start} -> {latest}; "
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
            game_type=game_type,
            from_date=from_date,
            to_date=to_date,
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
    ap.add_argument(
        "--game-type",
        dest="game_type",
        default=None,
        help='Queue all current-season finals with this FHM type (e.g. "Prospect Tournament"). Ignores --days.',
    )
    ap.add_argument(
        "--from-date",
        dest="from_date",
        default=None,
        help="Inclusive in-game start date (YYYY-MM-DD). Requires --to-date.",
    )
    ap.add_argument(
        "--to-date",
        dest="to_date",
        default=None,
        help="Inclusive in-game end date (YYYY-MM-DD). Requires --from-date.",
    )
    args = ap.parse_args()
    if args.days < 1:
        raise SystemExit("--days must be >= 1")
    from_d = _parse_iso_date(args.from_date, "--from-date")
    to_d = _parse_iso_date(args.to_date, "--to-date")
    if (from_d is None) ^ (to_d is None):
        raise SystemExit("Pass both --from-date and --to-date, or neither.")

    totals = {"games": 0, "queued": 0, "skipped": 0}
    for slug in _target_slugs(args):
        stats = _queue_league(
            slug,
            days=int(args.days),
            game_type=(str(args.game_type).strip() if args.game_type else None),
            from_date=from_d,
            to_date=to_d,
            dry_run=bool(args.dry_run),
            force=bool(args.force),
        )
        for k in totals:
            totals[k] += int(stats.get(k) or 0)
    print(f"done: {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
