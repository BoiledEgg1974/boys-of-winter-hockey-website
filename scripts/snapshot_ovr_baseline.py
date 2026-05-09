"""Snapshot composite OVR (1–100) into ``player_overall_baselines`` for the active league.

Run **before** replacing ``data/imports/raw/<league>/`` CSVs (or on the server before uploading
new CSVs) so depth-chart ↑/↓ arrows compare the previous site state to ratings after import.

Requires ``LEAGUE_SLUG`` (e.g. bowl-fantasy). Same data as ``flask bowl-overall-baseline-refresh``
but intended as a subprocess hook from STEP1 / deploy scripts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    slug = (os.environ.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        print("snapshot_ovr_baseline: set LEAGUE_SLUG (e.g. bowl-fantasy).", file=sys.stderr)
        return 1
    from app import create_app
    from app.config import make_league_config
    from app.models import db
    from app.services.player_overall_score import refresh_all_player_overall_baselines

    app = create_app(make_league_config(slug))
    with app.app_context():
        n = refresh_all_player_overall_baselines(db.session)
    print(f"snapshot_ovr_baseline ({slug}): stored baseline OVR for {n} players.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
