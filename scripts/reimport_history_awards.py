"""Reload ``history_awards`` from the CSV under ``RAW_IMPORT_DIR`` (see ``LEAGUE_SLUG``).

Run from repo root:

  PYTHONPATH=. python scripts/reimport_history_awards.py

Full replace (default): delete all ``history_awards`` rows, then import every CSV row.

Partial replace: delete DB rows whose ``award_name`` matches a substring, then import only
matching CSV rows (e.g. Jack Adams only):

  LEAGUE_SLUG=bowl-historical PYTHONPATH=. python scripts/reimport_history_awards.py --only-award "JACK ADAMS"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app import create_app
from scripts.import_pipeline.runner import import_history_awards


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("Run from repo root:", 1)[0].strip())
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
    app = create_app()
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
