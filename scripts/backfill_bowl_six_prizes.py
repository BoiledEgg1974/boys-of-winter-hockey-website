"""Backfill BOWL Six weekly and season AP prizes for one or all leagues.

Examples::

  PYTHONPATH=. python scripts/backfill_bowl_six_prizes.py
  PYTHONPATH=. python scripts/backfill_bowl_six_prizes.py bowl-cap
"""
from __future__ import annotations

import argparse
import sys

from app import create_app
from app.config import league_slugs, make_league_config
from app.services.bowl_six import (
    backfill_bowl_six_season_prizes,
    backfill_bowl_six_weekly_prizes,
)


def _run_for_slug(slug: str) -> None:
    app = create_app(make_league_config(slug))
    with app.app_context():
        from app.league_db import db

        weekly = backfill_bowl_six_weekly_prizes(db.session, db.session, slug)
        season = backfill_bowl_six_season_prizes(db.session, slug)
        db.session.commit()
        print(f"[{slug}] weekly: {weekly.get('message')}")
        print(f"[{slug}] season: {season.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "league_slug",
        nargs="?",
        help="League slug (default: all registered leagues)",
    )
    args = parser.parse_args(argv)
    slugs = [args.league_slug] if args.league_slug else league_slugs()
    unknown = [s for s in slugs if s not in league_slugs()]
    if unknown:
        print(f"Unknown league slug(s): {', '.join(unknown)}", file=sys.stderr)
        return 1
    for slug in slugs:
        _run_for_slug(slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
