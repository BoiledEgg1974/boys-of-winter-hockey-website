"""Reset current BOWL Six scores and apply a one-week scoring date override.

Example:

  python scripts/bowl_six_reset_current_week.py --all --start 2026-06-16 --end 2026-06-20

This preserves submitted lineups. It clears stored lineup scores, player-week
stats, and current-week game-final markers, then re-syncs final games that fall
inside the configured scoring dates.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import league_slugs, make_league_config
from app.league_db import db
from app.services.bowl_six import (
    _real_bowl_six_week_bounds,
    _sync_slate_week_final_markers,
    get_or_create_current_slate,
    refresh_player_week_stats,
    refresh_slate_lineup_scores,
    reset_slate_scoring_state,
)
from app.site_models import BowlSixSlate


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date {raw!r}; use YYYY-MM-DD") from exc


def _target_slugs(args: argparse.Namespace) -> list[str]:
    if args.all:
        return league_slugs()
    if args.league:
        return [str(args.league).strip()]
    raise SystemExit("Pass --all or --league <slug>.")


def _current_slate_for_slug(slug: str) -> BowlSixSlate:
    week_start, _week_end = _real_bowl_six_week_bounds()
    slate = db.session.scalar(
        select(BowlSixSlate)
        .where(BowlSixSlate.league_slug == slug, BowlSixSlate.week_start == week_start)
        .limit(1)
    )
    if slate is None:
        slate = get_or_create_current_slate(db.session, slug, league_session=db.session)
    if slate is None:
        raise SystemExit(f"BOWL Six is disabled or unavailable for {slug}.")
    if slate.status == "scored":
        raise SystemExit(
            f"Refusing to reset already-scored slate {slate.id} for {slug}; use admin rescore tools instead."
        )
    return slate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Apply to every configured league slug.")
    group.add_argument("--league", help="Apply to one league slug.")
    ap.add_argument("--start", required=True, type=_parse_date, help="Scoring start date, YYYY-MM-DD.")
    ap.add_argument("--end", required=True, type=_parse_date, help="Scoring end date, YYYY-MM-DD.")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = ap.parse_args()

    if args.end < args.start:
        raise SystemExit("--end must be on or after --start.")

    for slug in _target_slugs(args):
        os.environ["LEAGUE_SLUG"] = slug
        app = create_app(make_league_config(slug))
        with app.app_context():
            slate = _current_slate_for_slug(slug)
            previous = {
                "scoring_week_start": slate.scoring_week_start,
                "scoring_week_end": slate.scoring_week_end,
                "status": slate.status,
            }
            if args.dry_run:
                print(
                    f"[dry-run] {slug}: slate {slate.id} week {slate.week_start}..{slate.week_end} "
                    f"would change scoring dates {previous['scoring_week_start']}.."
                    f"{previous['scoring_week_end']} -> {args.start}..{args.end}"
                )
                db.session.rollback()
                continue

            slate.scoring_week_start = args.start
            slate.scoring_week_end = args.end
            reset = reset_slate_scoring_state(db.session, db.session, slate)
            markers_added = _sync_slate_week_final_markers(db.session, db.session, slate)
            scored = refresh_slate_lineup_scores(db.session, db.session, slate)
            refresh_player_week_stats(db.session, slate, db.session)

            try:
                from app.services.bowl_six_discord import maybe_enqueue_bowl_six_leaders_discord

                maybe_enqueue_bowl_six_leaders_discord(db.session, db.session, slate, force=True)
            except Exception:
                app.logger.exception("BOWL Six Discord leaders enqueue failed during score reset")

            db.session.commit()
            print(
                f"{slug}: reset slate {slate.id}; scoring dates {previous['scoring_week_start']}.."
                f"{previous['scoring_week_end']} -> {args.start}..{args.end}; "
                f"cleared {reset['lineup_scores']} score(s), {reset['player_week_stats']} player stat row(s), "
                f"{reset['game_markers']} marker(s); re-added {markers_added} marker(s); "
                f"recalculated {scored} lineup(s)."
            )


if __name__ == "__main__":
    main()
