#!/usr/bin/env python3
"""Apply DISCORD_* sim-log env defaults to site_membership.db (all hockey leagues)."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import create_app
from app.config import HOCKEY_LEAGUE_SLUGS, make_league_config
from app.league_db import db
from app.services.discord_events import bootstrap_discord_integration_all_leagues, ensure_discord_routes


def main() -> None:
    # Any hockey mount shares site_membership.db (site bind).
    app = create_app(make_league_config("bowl-fantasy"))
    with app.app_context():
        bootstrap_discord_integration_all_leagues(db.session)
        for slug in sorted(HOCKEY_LEAGUE_SLUGS):
            ensure_discord_routes(db.session, slug)
        db.session.commit()
        print("Applied Discord sim-log env defaults for configured hockey leagues.")


if __name__ == "__main__":
    main()
