"""Revoke invalid The Heist unlocks (season totals after a trade) in every hockey league.

Run on the live host so site MySQL and each league SQLite are the production files::

    python scripts/clawback_invalid_heists.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import HOCKEY_LEAGUE_SLUGS, make_league_config
from app.services.gm_achievements import clawback_invalid_heists


def main() -> int:
    reports: list[dict] = []
    for slug in sorted(HOCKEY_LEAGUE_SLUGS):
        app = create_app(make_league_config(slug))
        with app.app_context():
            report = clawback_invalid_heists(app)
        reports.append(report)
        print(f"{slug}: revoked={report.get('revoked', 0)} skipped={report.get('skipped', 0)}")
        for row in report.get("details") or []:
            print(
                f"  team {row.get('team_id')} {row.get('achievement_key')}: "
                f"{row.get('detail') or row.get('player_name') or 'The Heist'} "
                f"(claimed={row.get('claimed')} ap={row.get('ap_delta')})"
            )
    print(json.dumps(reports, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
