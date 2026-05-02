"""Align BOWL-Historical ``Season`` rows for 1968–69 as current (and 1967–68 not current).

Run after editing ``data/imports/raw/bowl_historical/seasons.csv`` or to fix an existing DB
without re-running the full import:

  LEAGUE_SLUG=bowl-historical PYTHONPATH=. python scripts/set_historical_current_season.py

Uses the same league DB as ``create_app(make_league_config('bowl-historical'))``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.config import make_league_config  # noqa: E402
from app.models import Season, db  # noqa: E402


def main() -> None:
    slug = (os.environ.get("LEAGUE_SLUG") or "").strip() or "bowl-historical"
    if slug != "bowl-historical":
        print("This script is intended for LEAGUE_SLUG=bowl-historical.", file=sys.stderr)
        print(f"Got: {slug!r}. Set LEAGUE_SLUG=bowl-historical or pass nothing (defaults).", file=sys.stderr)
        sys.exit(1)
    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    with app.app_context():
        # Match by canonical labels only so we never treat one row as both seasons.
        prev = db.session.scalars(select(Season).where(Season.label == "1967-68").limit(1)).first()
        if prev:
            prev.is_current = False
            prev.start_year = 1967
            prev.end_year = 1968
            print(f"Set is_current=False: id={prev.id} label={prev.label!r}")
        else:
            print("No season with label '1967-68'; skipping prior-year update.")

        nxt = db.session.scalars(select(Season).where(Season.label == "1968-69").limit(1)).first()
        if nxt:
            if prev is not None and nxt.id == prev.id:
                print(
                    "ERROR: 1967-68 and 1968-69 resolved to the same row; fix the DB or re-import "
                    "data/imports/raw/bowl_historical/seasons.csv (import_seasons).",
                    file=sys.stderr,
                )
                sys.exit(2)
            nxt.start_year = 1968
            nxt.end_year = 1969
            nxt.is_current = True
            if not (nxt.fhm_season_id or "").strip():
                nxt.fhm_season_id = "fhm-league-1968-69"
            print(f"Set is_current=True: id={nxt.id} label={nxt.label!r} start_year={nxt.start_year}")
        else:
            nxt = Season(
                label="1968-69",
                fhm_season_id="fhm-league-1968-69",
                start_year=1968,
                end_year=1969,
                is_current=True,
            )
            db.session.add(nxt)
            print("Inserted new season: 1968-69 (start_year=1968, is_current=True)")

        db.session.commit()
        print("Committed.")


if __name__ == "__main__":
    main()
