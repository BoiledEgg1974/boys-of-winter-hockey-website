"""Reload ``history_awards`` from the CSV under ``RAW_IMPORT_DIR`` (see league slug).

Run from repo root (same league selection as ``scripts/import_data.py``):

  PYTHONPATH=. python scripts/reimport_history_awards.py bowl-cap

  # or: set LEAGUE_SLUG then run without positional

  Uses ``make_league_config`` so the correct league SQLite file and raw folder are used.
  Plain ``create_app()`` would follow ``DATABASE_URL`` from ``.env`` and can refresh the wrong DB.

Full replace (default): delete all ``history_awards`` rows, then import every CSV row.

Partial replace: delete DB rows whose ``award_name`` matches a substring, then import only
matching CSV rows (e.g. Jack Adams only):

  PYTHONPATH=. python scripts/reimport_history_awards.py bowl-historical --only-award "JACK ADAMS"
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
from scripts.import_pipeline.runner import import_history_awards


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("Run from repo root", 1)[0].strip())
    ap.add_argument(
        "league_positional",
        nargs="?",
        default="",
        metavar="LEAGUE",
        help="League slug: bowl-cap, bowl-fantasy, or bowl-historical (same as import_data.py).",
    )
    ap.add_argument(
        "-l",
        "--league",
        dest="league_flag",
        default="",
        help="Same as positional LEAGUE.",
    )
    ap.add_argument(
        "--only-award",
        metavar="SUBSTRING",
        default=None,
        help="Case-insensitive substring of award_name: remove matching DB rows, import only matching CSV rows.",
    )
    ap.add_argument(
        "--csv-path",
        type=Path,
        default=None,
        help="Explicit history awards CSV (default: discover under RAW_IMPORT_DIR).",
    )
    args = ap.parse_args()
    chosen = (args.league_flag or args.league_positional or "").strip()
    if chosen:
        os.environ["LEAGUE_SLUG"] = chosen
    slug = (os.environ.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        ap.error(
            "missing league: pass bowl-cap / bowl-fantasy / bowl-historical "
            "(positional or -l), or set LEAGUE_SLUG (see scripts/import_data.py)."
        )
    app = create_app(make_league_config(slug))
    with app.app_context():
        raw = Path(str(app.config["RAW_IMPORT_DIR"]))
        only = (args.only_award or "").strip()
        csv_p = args.csv_path.resolve() if args.csv_path else None
        if only:
            n = import_history_awards(raw, app, csv_path=csv_p, replace_award_substring=only)
            print(f"Imported {n} history_awards row(s) matching {only!r} from {csv_p or raw}.")
        else:
            n = import_history_awards(raw, app, csv_path=csv_p, replace_all=True)
            print(f"Imported {n} history_awards row(s) from {csv_p or raw}.")


if __name__ == "__main__":
    main()
