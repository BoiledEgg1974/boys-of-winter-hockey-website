"""Evaluate GM achievements after league SQLite is current (deploy-db / live promote).

Local FHM import updates ``instance/site_membership.db`` only. Production uses
``SITE_DATABASE_URL`` (MySQL on the VPS). ``deploy-db`` uploads league DBs, not
the site DB, so achievements must run again with each league's SQLite + live site DB.

  python scripts/run_gm_achievements_after_deploy.py
  python scripts/run_gm_achievements_after_deploy.py --league bowl-fantasy
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_log = logging.getLogger("bowl.gm_achievements_deploy")


def _slugs(args: argparse.Namespace) -> list[str]:
    from app.config import HOCKEY_LEAGUE_SLUGS, league_slugs

    if args.league:
        return [str(args.league).strip()]
    ordered = [s for s in league_slugs() if s in HOCKEY_LEAGUE_SLUGS]
    return ordered or sorted(HOCKEY_LEAGUE_SLUGS)


def _evaluate_league(slug: str) -> dict:
    from app import create_app
    from app.config import make_league_config
    from app.services.gm_achievements import evaluate_gm_achievements_after_import

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    with app.app_context():
        stats = evaluate_gm_achievements_after_import(app)
    out = {"league_slug": slug, **{k: int(v or 0) for k, v in stats.items() if isinstance(v, int)}}
    print(f"{slug}: GM achievements {out}")
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", help="Single hockey league slug (default: all hockey).")
    args = ap.parse_args()
    failed = 0
    for slug in _slugs(args):
        try:
            _evaluate_league(slug)
        except Exception:
            _log.exception("GM achievements after deploy failed for %s", slug)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
