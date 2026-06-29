"""Apply idempotent league SQLite schema migrations (e.g. after backup restore).

Run on PythonAnywhere with the web app reloaded and the Discord bot stopped::

    python scripts/apply_league_migrations.py bowl-historical

If migrations were skipped because of a stale bootstrap marker::

    rm -f instance/bowl-historical.db.bootstrap.version
    python scripts/apply_league_migrations.py bowl-historical
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import make_league_config, resolve_league_sqlite_path  # noqa: E402
from app.db_utils import prepare_sqlite_database  # noqa: E402
from app.sqlite_bootstrap import apply_league_sqlite_migrations  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("league", nargs="?", default="", help="League slug (bowl-historical, bowl-cap, bowl-fantasy)")
    args = p.parse_args()
    slug = (args.league or os.environ.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        p.error("pass league slug or set LEAGUE_SLUG")
    os.environ["LEAGUE_SLUG"] = slug

    db_path = resolve_league_sqlite_path(slug)
    if not db_path.is_file():
        print(f"ERROR: league database not found: {db_path}", file=sys.stderr)
        return 1
    healthy, msg = prepare_sqlite_database(db_path, auto_repair=False)
    if not healthy:
        print(f"ERROR: integrity_check failed for {db_path}: {msg}", file=sys.stderr)
        return 1

    app = create_app(make_league_config(slug))
    with app.app_context():
        apply_league_sqlite_migrations(app)
    print(f"League migrations applied for {slug} ({db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
