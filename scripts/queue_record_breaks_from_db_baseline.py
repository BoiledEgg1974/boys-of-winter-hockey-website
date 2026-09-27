"""Queue record-broken Discord events by diffing a baseline SQLite file against the live league DB.

Used when deploy-db suppressed breaks (over the enqueue cap) or a notify step was skipped.
The baseline should be record snapshots from *before* the import (e.g. legacy ``league3.db``
vs current ``bowl-cap.db`` on Cap).

  python scripts/queue_record_breaks_from_db_baseline.py --league bowl-cap --baseline-db instance/league3.db
  python scripts/queue_record_breaks_from_db_baseline.py --league bowl-cap --baseline-db instance/league3.db --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _league_config_for_db(slug: str, db_path: Path):
    from app.config import make_league_config

    base = make_league_config(slug)

    class _Cfg(base):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.resolve()}"

    return _Cfg


def _collect_state(db_path: Path, slug: str) -> dict:
    from app import create_app
    from app.league_db import db
    from app.services.record_broken_discord import collect_live_record_state

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(_league_config_for_db(slug, db_path))
    with app.app_context():
        return collect_live_record_state(db.session)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", required=True, help="League slug (e.g. bowl-cap).")
    ap.add_argument(
        "--baseline-db",
        required=True,
        type=Path,
        help="SQLite file with pre-import record snapshots / game baselines.",
    )
    ap.add_argument(
        "--current-db",
        type=Path,
        default=None,
        help="Current league DB (default: resolve_league_sqlite_path for --league).",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    slug = str(args.league).strip()
    baseline = args.baseline_db.expanduser().resolve()
    if not baseline.is_file():
        print(f"Baseline DB not found: {baseline}", file=sys.stderr)
        return 1

    from app.config import resolve_league_sqlite_path

    current = (args.current_db or resolve_league_sqlite_path(slug)).expanduser().resolve()
    if not current.is_file():
        print(f"Current DB not found: {current}", file=sys.stderr)
        return 1

    live_state = _collect_state(baseline, slug)
    snap_n = len(live_state.get("snapshots") or {})
    gb_n = len(live_state.get("game_baselines") or {})
    print(f"Baseline {baseline.name}: snapshots={snap_n} game_baselines={gb_n}")

    from app import create_app
    from app.config import make_league_config
    from app.league_db import db
    from app.services.record_broken_discord import (
        enqueue_record_broken_events_from_deploy,
        events_from_live_record_state_diff,
    )
    from app.sqlite_retry import commit_with_sqlite_retry

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(_league_config_for_db(slug, current))
    with app.app_context():
        events = events_from_live_record_state_diff(
            db.session,
            league_slug=slug,
            live_state=live_state,
        )
        print(f"Diff vs {current.name}: {len(events)} record-break event(s)")
        if args.dry_run:
            for ev in events[:20]:
                p = ev.get("payload") or {}
                print(f"  {ev.get('source_id')}: {p.get('title') or p.get('record_title')}")
            if len(events) > 20:
                print(f"  ... and {len(events) - 20} more")
            return 0

        stats = enqueue_record_broken_events_from_deploy(
            db.session,
            league_slug=slug,
            events=events,
        )
        commit_with_sqlite_retry(db.session)
        print(f"Enqueue result: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
