"""Run CSV import pipeline.

Usage (pick one):

  python scripts/import_data.py bowl-cap
  python scripts/import_data.py --league bowl-fantasy
  set LEAGUE_SLUG=bowl-cap && python scripts/import_data.py   # Windows cmd

The league argument is **required** unless ``LEAGUE_SLUG`` is already set in the
environment, so you do not accidentally refresh the wrong SQLite file while testing
another league in the browser (e.g. ``/bowl-cap/`` vs ``/bowl-fantasy/``).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_pipeline.runner import run_import  # noqa: E402


def _parse_args() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "league_positional",
        nargs="?",
        default="",
        metavar="LEAGUE",
        help="League slug: bowl-cap, bowl-fantasy, or bowl-historical",
    )
    p.add_argument(
        "-l",
        "--league",
        dest="league_flag",
        default="",
        help="Same as positional LEAGUE (optional alternative)",
    )
    args = p.parse_args()
    chosen = (args.league_flag or args.league_positional or "").strip()
    if chosen:
        os.environ["LEAGUE_SLUG"] = chosen
    if not (os.environ.get("LEAGUE_SLUG") or "").strip():
        p.error(
            "missing league: pass bowl-cap / bowl-fantasy / bowl-historical, "
            "or set LEAGUE_SLUG before running (see scripts/import_cap.cmd)."
        )


if __name__ == "__main__":
    _parse_args()
    run_import()
