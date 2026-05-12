"""Reload ``history_all_stars`` from ``RAW_IMPORT_DIR/history_all_stars.csv`` (see league slug).

Run from repo root:

  PYTHONPATH=. python scripts/reimport_history_all_stars.py bowl-cap

Uses ``make_league_config`` so the league SQLite file and raw folder match the mounted app.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import make_league_config
from scripts.import_pipeline.runner import import_history_all_stars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip())
    ap.add_argument(
        "league_positional",
        nargs="?",
        default="",
        metavar="LEAGUE",
        help="League slug: bowl-cap, bowl-fantasy, or bowl-historical.",
    )
    ap.add_argument(
        "-l",
        "--league",
        dest="league_flag",
        default="",
        help="Same as positional LEAGUE.",
    )
    args = ap.parse_args()
    chosen = (args.league_flag or args.league_positional or "").strip()
    if chosen:
        os.environ["LEAGUE_SLUG"] = chosen
    slug = (os.environ.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        ap.error("missing league: pass bowl-cap / bowl-fantasy / bowl-historical or set LEAGUE_SLUG.")
    app = create_app(make_league_config(slug))
    with app.app_context():
        raw = Path(str(app.config["RAW_IMPORT_DIR"]))
        n = import_history_all_stars(raw, app)
        print(f"Imported {n} history_all_stars row(s) from {raw / 'history_all_stars.csv'}.")


if __name__ == "__main__":
    main()
