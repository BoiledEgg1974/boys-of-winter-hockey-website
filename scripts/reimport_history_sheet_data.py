"""Re-apply League History CSV overlays (awards + all-stars) for one league.

Intended to run immediately after ``scripts/import_data.py`` in STEP1 / STEP2 so
``/history`` award cards and all-star tables always match the CSVs under
``RAW_IMPORT_DIR``, regardless of FHM vs classic STEPS import paths.

  PYTHONPATH=. python scripts/reimport_history_sheet_data.py bowl-cap

If ``history_awards*.csv`` is absent, awards are skipped. If ``history_all_stars.csv``
is absent, that step is skipped (table left unchanged).
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
from scripts.import_pipeline.runner import (
    _history_awards_csv_path,
    import_history_all_stars,
    import_history_awards,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Re-apply history_awards + history_all_stars from RAW_IMPORT_DIR for one league "
            "(run after import_data.py; see module docstring)."
        ),
    )
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
        n_aw = 0
        p_aw = _history_awards_csv_path(raw)
        if p_aw is not None and p_aw.is_file():
            n_aw = import_history_awards(raw, app, csv_path=p_aw, replace_all=True)
        n_as = import_history_all_stars(raw, app)
        print(
            f"reimport_history_sheet_data {slug}: "
            f"history_awards={n_aw} row(s); history_all_stars={n_as} row(s)."
        )


if __name__ == "__main__":
    main()
