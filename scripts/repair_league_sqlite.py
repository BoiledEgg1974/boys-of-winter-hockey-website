"""Diagnose and repair corrupted league SQLite databases.

Run on PythonAnywhere **after reloading the web app** (and pausing the Discord bot)
so no worker holds the DB open during repair.

Examples::

    python scripts/repair_league_sqlite.py --check
    python scripts/repair_league_sqlite.py --repair
    python scripts/repair_league_sqlite.py --repair --league bowl-cap
    python scripts/repair_league_sqlite.py --reset-snapshots --league bowl-cap
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    _LEGACY_LEAGUE_DB_FILES,
    league_slugs,
    resolve_league_sqlite_path,
    resolve_site_sqlite_path,
)
from app.db_utils import (  # noqa: E402
    recover_sqlite_database,
    reset_player_rating_snapshots_sqlite,
    sqlite_integrity_message,
    sqlite_is_healthy,
    sqlite_wal_checkpoint,
)


def _candidate_db_paths() -> list[tuple[str, Path]]:
    inst = ROOT / "instance"
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []

    def add(label: str, path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        out.append((label, resolved))

    add("site_membership", resolve_site_sqlite_path())
    for slug in league_slugs():
        add(slug, resolve_league_sqlite_path(slug))
        legacy_name = _LEGACY_LEAGUE_DB_FILES.get(slug)
        if legacy_name:
            add(f"{slug} (legacy {legacy_name})", inst / legacy_name)
    for path in sorted(inst.glob("*.db")):
        add(path.name, path)
    return out


def _reset_snapshots(path: Path) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path.resolve().as_posix()}")
    reset_player_rating_snapshots_sqlite(engine)
    engine.dispose()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--check",
        action="store_true",
        help="Run integrity_check only (default when neither --repair nor --reset-snapshots).",
    )
    p.add_argument(
        "--repair",
        action="store_true",
        help="Rebuild corrupt databases via sqlite dump/recover (backs up first).",
    )
    p.add_argument(
        "--reset-snapshots",
        action="store_true",
        help="Drop/recreate player_rating_snapshots only (derived trend data).",
    )
    p.add_argument(
        "--league",
        default="",
        help="Limit to one league slug (e.g. bowl-cap) or 'site' for site_membership.db.",
    )
    args = p.parse_args()
    do_check = args.check or (not args.repair and not args.reset_snapshots)
    league_filter = str(args.league or "").strip().lower()

    paths = _candidate_db_paths()
    if league_filter:
        if league_filter == "site":
            paths = [(label, path) for label, path in paths if "site_membership" in label]
        else:
            paths = [
                (label, path)
                for label, path in paths
                if league_filter in label.lower()
            ]
    if not paths:
        print("No matching database files found under instance/.", file=sys.stderr)
        return 1

    exit_code = 0
    for label, path in paths:
        print(f"\n=== {label} ===")
        print(f"  path: {path}")
        sqlite_wal_checkpoint(path)
        msg = sqlite_integrity_message(path)
        healthy = msg.lower() == "ok"
        print(f"  integrity_check: {msg}")
        if healthy and not args.repair and not args.reset_snapshots:
            continue
        if not healthy and do_check and not args.repair:
            exit_code = 1
        if args.reset_snapshots:
            print("  resetting player_rating_snapshots …")
            _reset_snapshots(path)
            msg = sqlite_integrity_message(path)
            print(f"  integrity_check after snapshot reset: {msg}")
            if msg.lower() != "ok":
                exit_code = 1
        if args.repair and not healthy:
            print("  repairing database (backup + rebuild) …")
            try:
                backup = recover_sqlite_database(path)
            except Exception as exc:
                print(f"  REPAIR FAILED: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            print(f"  backup: {backup.name}")
            msg = sqlite_integrity_message(path)
            print(f"  integrity_check after repair: {msg}")
            if msg.lower() != "ok":
                exit_code = 1
            else:
                print("  repaired OK")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
