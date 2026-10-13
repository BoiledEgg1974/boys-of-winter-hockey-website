"""Revoke Kiss from a Rose unlocks that used the wrong season/result metadata.

Run on the live host (site DB + league SQLite), then re-run GM achievements for the league::

    python scripts/clawback_invalid_make_playoffs.py
    python scripts/run_gm_achievements_after_deploy.py --league bowl-cap
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
from app.services.gm_achievements import clawback_invalid_make_playoffs_unlocks


def main() -> int:
    reports: list[dict] = []
    for slug in sorted(HOCKEY_LEAGUE_SLUGS):
        app = create_app(make_league_config(slug))
        with app.app_context():
            report = clawback_invalid_make_playoffs_unlocks(app)
        reports.append(report)
        print(f"{slug}: revoked={report.get('revoked', 0)} skipped={report.get('skipped', 0)}")
        for row in report.get("details") or []:
            print(
                f"  team {row.get('team_id')} {row.get('achievement_key')}: "
                f"season={row.get('season')!r} detail={row.get('detail')!r} "
                f"(claimed={row.get('claimed')} ap={row.get('ap_delta')})"
            )
    print(json.dumps(reports, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
