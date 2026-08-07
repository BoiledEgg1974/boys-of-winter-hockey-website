"""Import Formula BOWL / Demolition BOWL roster.txt + CSVs from data/imports/raw/<league>.

Usage::

    python scripts/import_racing_data.py
    python scripts/import_racing_data.py bowl-formula
    python scripts/import_racing_data.py bowl-demolition --no-discord
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _import_one(slug: str, *, enqueue_discord: bool) -> int:
    from app import create_app
    from app.config import is_racing_league, make_league_config
    from app.models import db
    from app.services.racing_discord import enqueue_after_import
    from app.services.racing_import import import_all_from_raw_dir
    from app.sqlite_retry import commit_with_sqlite_retry

    if not is_racing_league(slug):
        print(f"Not a racing league: {slug}", file=sys.stderr)
        return 1
    app = create_app(make_league_config(slug))
    with app.app_context():
        results = import_all_from_raw_dir(db.session, league_slug=slug)
        if enqueue_discord:
            enqueue_after_import(db.session, league_slug=slug, import_results=results)
        commit_with_sqlite_retry(db.session)
    ok = sum(1 for r in results if not r.get("error") and not r.get("skipped"))
    err = sum(1 for r in results if r.get("error"))
    print(f"import_racing_data ({slug}): {len(results)} file(s), {ok} applied, {err} error(s).")
    for r in results:
        kind = r.get("kind") or "?"
        name = r.get("file") or ""
        if r.get("error"):
            print(f"  ! {name} ({kind}): {r.get("error")}")
        elif r.get("skipped"):
            print(f"  - {name} ({kind}): skipped ({r.get("reason") or "n/a"})")
        else:
            print(f"  + {name} ({kind})")
    return 1 if err else 0


def main() -> int:
    from app.config import RACING_LEAGUE_SLUGS

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "leagues",
        nargs="*",
        default=sorted(RACING_LEAGUE_SLUGS),
        help="Racing league slug(s). Default: all racing leagues.",
    )
    ap.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip Discord enqueue after import.",
    )
    args = ap.parse_args()
    rc = 0
    for slug in args.leagues:
        slug = str(slug or "").strip()
        if not slug:
            continue
        try:
            step_rc = _import_one(slug, enqueue_discord=not args.no_discord)
        except Exception as exc:
            print(f"import_racing_data ({slug}) failed: {exc}", file=sys.stderr)
            step_rc = 1
        rc = rc or step_rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
