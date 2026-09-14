"""Backfill permanent player_season_war rows from career lines (Legacy formula).

Usage:
  python scripts/backfill_war_history.py --all
  python scripts/backfill_war_history.py bowl-historical
  python scripts/backfill_war_history.py bowl-cap --segment rs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app import create_app
from app.config import LEAGUES, make_league_config
from app.league_db import db
from app.models import PlayerGoalieCareerLine, PlayerSeasonWar, PlayerSkaterCareerLine
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.season_war import backfill_legacy_season_war, upsert_live_season_war
from app.services.seasons import get_current_season


def _season_years(session) -> list[int]:
    main_ids = bowl_nhl_league_ids(session)
    if not main_ids:
        return []
    sk_years = session.scalars(
        select(PlayerSkaterCareerLine.season_year)
        .where(PlayerSkaterCareerLine.league_fhm_id.in_(main_ids))
        .distinct()
    ).all()
    gk_years = session.scalars(
        select(PlayerGoalieCareerLine.season_year)
        .where(PlayerGoalieCareerLine.league_fhm_id.in_(main_ids))
        .distinct()
    ).all()
    years = sorted({int(y) for y in sk_years if y is not None} | {int(y) for y in gk_years if y is not None})
    return years


def _backfill_league(slug: str, *, segments: tuple[str, ...], live: bool) -> None:
    app = create_app(make_league_config(slug))
    with app.app_context():
        raw_dir = Path(app.config["RAW_IMPORT_DIR"])
        season = get_current_season()
        current_year = int(season.start_year) if season and season.start_year is not None else None
        years = _season_years(db.session)
        if live and season is not None:
            n = upsert_live_season_war(
                db.session,
                league_slug=slug,
                season=season,
                raw_dir=raw_dir,
                is_finalized=False,
            )
            print(f"{slug}: live season WAR upserted {n} row(s)")
        total = 0
        for year in years:
            if current_year is not None and year == current_year:
                continue
            for segment in segments:
                total += backfill_legacy_season_war(
                    db.session,
                    league_slug=slug,
                    season_year=int(year),
                    segment=segment,
                )
        count = db.session.scalar(select(func.count()).select_from(PlayerSeasonWar)) or 0
        print(f"{slug}: backfilled legacy rows (batch={total}); table now has {count} row(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill player_season_war from career CSV history")
    parser.add_argument("leagues", nargs="*", help="League slug(s)")
    parser.add_argument("--all", action="store_true", help="All configured hockey leagues")
    parser.add_argument(
        "--segment",
        choices=("rs", "ps", "po", "all"),
        default="all",
        help="Stat segment to backfill (default: all)",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Skip upserting current live season from PlayerSkaterStat",
    )
    args = parser.parse_args()
    slugs = [e.slug for e in LEAGUES if e.slug.startswith("bowl-") and "formula" not in e.slug and "demolition" not in e.slug]
    if args.all:
        targets = slugs
    else:
        targets = list(args.leagues)
    if not targets:
        parser.error("Pass league slug(s) or --all")
    segments: tuple[str, ...]
    if args.segment == "all":
        segments = ("rs", "ps", "po")
    else:
        segments = (args.segment,)
    for slug in targets:
        _backfill_league(slug, segments=segments, live=not args.no_live)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
