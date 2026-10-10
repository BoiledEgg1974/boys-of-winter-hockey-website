#!/usr/bin/env python3
"""Queue a BLUP/BLOW injury report post to Discord (#injury-list on Relegation).

Run on the VPS after fixing routes or when catching up a missed import::

    cd /srv/bowl/app && sudo -u bowl .venv/bin/python scripts/publish_relegation_injury_discord.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.services.injury_discord import maybe_enqueue_injury_report_delta
from app.sqlite_retry import commit_with_sqlite_retry


def main() -> int:
    app = create_app(make_league_config("bowl-fantasy"))
    with app.app_context():
        ok = maybe_enqueue_injury_report_delta(db.session, "bowl-fantasy", force=True)
        if ok:
            commit_with_sqlite_retry(db.session)
            print("Queued injury_report_delta for bowl-fantasy.")
            return 0
        print("Did not queue injury report (see app logs).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
