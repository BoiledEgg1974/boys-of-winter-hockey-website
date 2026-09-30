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
    import time

    from sqlalchemy import inspect
    from sqlalchemy.exc import OperationalError

    slug = (os.environ.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        print("snapshot_ovr_baseline: set LEAGUE_SLUG (e.g. bowl-fantasy).", file=sys.stderr)
        return 1

    from app import create_app
    from app.config import make_league_config
    from app.league_db import db
    from app.services.player_overall_score import refresh_all_player_overall_baselines

    app = create_app(make_league_config(slug))
    try:
        with app.app_context():
            if not inspect(db.session.get_bind()).has_table("players"):
                print(
                    f"snapshot_ovr_baseline ({slug}): skipped (no players table — fresh or empty DB).",
                    file=sys.stderr,
                )
                return 0
            n = 0
            last_exc: OperationalError | None = None
            for attempt in range(20):
                try:
                    n = refresh_all_player_overall_baselines(db.session)
                    break
                except OperationalError as exc:
                    msg = str(exc).lower()
                    if "database is locked" not in msg and "locked" not in msg:
                        raise
                    last_exc = exc
                    db.session.rollback()
                    db.engine.dispose()
                    time.sleep(0.35 * (attempt + 1))
            else:
                assert last_exc is not None
                raise last_exc
    finally:
        try:
            db.session.remove()
            db.engine.dispose()
        except Exception:
            pass
    print(f"snapshot_ovr_baseline ({slug}): stored baseline OVR for {n} players.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
