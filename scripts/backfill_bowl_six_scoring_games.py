"""One-off BOWL Six scoring backfill from an in-game date range.

Example:

  python scripts/backfill_bowl_six_scoring_games.py bowl-historical --start 1969-03-10 --end 1969-03-16

By default this replaces the active slate's current real-time scoring markers for the
league, then re-scores the slate and queues a Discord leaders refresh.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Game
from app.services.bowl_six import (
    refresh_player_week_stats,
    refresh_slate_lineup_scores,
    record_bowl_six_game_finals,
    slate_real_scoring_window_utc,
)
from app.services.postseason_odds import _is_regular_season_game
from app.services.seasons import get_current_season
from app.site_models import BowlSixGameFinal, BowlSixSlate


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date {raw!r}; use YYYY-MM-DD") from exc


def _observed_at_inside_window(slate: BowlSixSlate) -> datetime:
    start, end = slate_real_scoring_window_utc(slate)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if start <= now < end:
        return now
    return start + timedelta(minutes=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("league", help="League slug, e.g. bowl-historical")
    ap.add_argument("--start", required=True, type=_parse_date, help="In-game start date")
    ap.add_argument("--end", required=True, type=_parse_date, help="In-game end date")
    ap.add_argument("--slate-id", type=int, default=0, help="Optional slate id; defaults to latest active slate")
    ap.add_argument(
        "--append",
        action="store_true",
        help="Append markers instead of replacing markers in the active real-time scoring window.",
    )
    args = ap.parse_args()

    slug = str(args.league or "").strip()
    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))

    with app.app_context():
        if args.slate_id:
            slate = db.session.get(BowlSixSlate, int(args.slate_id))
            if slate is None or slate.league_slug != slug:
                raise SystemExit(f"No slate {args.slate_id} for {slug}.")
        else:
            slate = db.session.scalar(
                select(BowlSixSlate)
                .where(
                    BowlSixSlate.league_slug == slug,
                    BowlSixSlate.status.in_(("open", "locked")),
                )
                .order_by(BowlSixSlate.week_start.desc())
                .limit(1)
            )
            if slate is None:
                raise SystemExit(f"No active BOWL Six slate found for {slug}.")

        season = get_current_season()
        if season is None:
            raise SystemExit("No current season found.")

        games = list(
            db.session.scalars(
                select(Game).where(
                    Game.season_id == int(season.id),
                    Game.game_date.isnot(None),
                    Game.game_date >= args.start,
                    Game.game_date <= args.end,
                    Game.status == "final",
                )
            ).all()
        )
        game_ids = [int(g.id) for g in games if _is_regular_season_game(g.game_type)]
        if not game_ids:
            raise SystemExit(
                f"No final regular-season games found for {slug} from {args.start} to {args.end}."
            )

        window_start, window_end = slate_real_scoring_window_utc(slate)
        removed = 0
        if not args.append:
            result = db.session.execute(
                delete(BowlSixGameFinal).where(
                    BowlSixGameFinal.league_slug == slug,
                    BowlSixGameFinal.first_final_at >= window_start,
                    BowlSixGameFinal.first_final_at < window_end,
                )
            )
            removed = int(result.rowcount or 0)

        added = record_bowl_six_game_finals(
            db.session,
            db.session,
            league_slug=slug,
            game_ids=game_ids,
            observed_at=_observed_at_inside_window(slate),
        )
        scored = refresh_slate_lineup_scores(db.session, db.session, slate)
        refresh_player_week_stats(db.session, slate, db.session)

        try:
            from app.services.bowl_six_discord import maybe_enqueue_bowl_six_leaders_discord

            maybe_enqueue_bowl_six_leaders_discord(db.session, db.session, slate, force=True)
        except Exception:
            app.logger.exception("BOWL Six Discord leaders enqueue failed during backfill")

        db.session.commit()
        print(
            f"Backfilled BOWL Six slate {slate.id} for {slug}: "
            f"{len(game_ids)} game(s) from {args.start}..{args.end}, "
            f"removed {removed} marker(s), added {added}, re-scored {scored} lineup(s)."
        )


if __name__ == "__main__":
    main()
