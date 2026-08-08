"""Enqueue Discord boxscores + BOWL Six leaders after deploy-db promotes league DBs.

Runs on PythonAnywhere (or locally against a site DB with real Discord routes).

Prefers ``instance/.deploy_discord_finals/<slug>.json`` game ids written during
local FHM import. When no sidecar exists, falls back to recent undelivered
finals (same as Admin → Queue recent boxscores, without force).

  python scripts/notify_discord_after_db_deploy.py
  python scripts/notify_discord_after_db_deploy.py --league bowl-historical
  python scripts/notify_discord_after_db_deploy.py --fallback-days 7
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

_log = logging.getLogger("bowl.deploy_discord")


def _slugs(args: argparse.Namespace) -> list[str]:
    from app.config import HOCKEY_LEAGUE_SLUGS, league_slugs

    if args.league:
        return [str(args.league).strip()]
    # Hockey mounts only — racing has its own Discord enqueue on import.
    ordered = [s for s in league_slugs() if s in HOCKEY_LEAGUE_SLUGS]
    return ordered or sorted(HOCKEY_LEAGUE_SLUGS)


def _notify_league(slug: str, *, fallback_days: int, dry_run: bool) -> dict:
    from app import create_app
    from app.config import make_league_config
    from app.league_db import db
    from app.services.bowl_six import refresh_bowl_six_leaders_for_discord_poll
    from app.services.deploy_discord_finals import (
        clear_deploy_newly_final_game_ids,
        load_deploy_newly_final_game_ids,
    )
    from app.services.game_boxscore_discord import (
        notify_game_boxscores_after_import,
        queue_recent_game_boxscores,
    )
    from app.services.playoff_discord_bracket import maybe_enqueue_playoff_bracket_discord
    from app.sqlite_retry import commit_with_sqlite_retry

    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    out: dict = {
        "league_slug": slug,
        "boxscore": {},
        "bowl_six": False,
        "playoff_bracket": None,
        "sidecar_ids": 0,
        "cleared_sidecar": False,
    }
    with app.app_context():
        ids = load_deploy_newly_final_game_ids(slug)
        out["sidecar_ids"] = len(ids)
        if dry_run:
            if ids:
                out["boxscore"] = {
                    "mode": "sidecar",
                    "game_ids": sorted(ids),
                    "queued": 0,
                }
            else:
                out["boxscore"] = {
                    "mode": "fallback_recent",
                    "days": fallback_days,
                    "queued": 0,
                }
            print(f"{slug}: dry-run {out}")
            return out

        if ids:
            box_stats = notify_game_boxscores_after_import(
                db.session,
                db.session,
                league_slug=slug,
                game_ids=ids,
            )
            out["boxscore"] = {"mode": "sidecar", **box_stats}
            out["cleared_sidecar"] = clear_deploy_newly_final_game_ids(slug)
        else:
            box_stats = queue_recent_game_boxscores(
                db.session,
                db.session,
                league_slug=slug,
                days=fallback_days,
                force=False,
            )
            out["boxscore"] = {"mode": "fallback_recent", **box_stats}

        try:
            out["bowl_six"] = bool(
                refresh_bowl_six_leaders_for_discord_poll(db.session, db.session, slug)
            )
        except Exception:
            _log.exception("BOWL Six Discord refresh failed for %s", slug)
            db.session.rollback()

        try:
            br = maybe_enqueue_playoff_bracket_discord(db.session, db.session, slug)
            out["playoff_bracket"] = br
        except Exception:
            _log.exception("Playoff bracket Discord enqueue failed for %s", slug)
            db.session.rollback()

        commit_with_sqlite_retry(db.session)
        print(
            f"{slug}: boxscore={out['boxscore']} bowl_six={out['bowl_six']} "
            f"sidecar_ids={out['sidecar_ids']} cleared={out['cleared_sidecar']}"
        )
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", help="Single hockey league slug (default: all hockey).")
    ap.add_argument(
        "--fallback-days",
        type=int,
        default=7,
        help="When no deploy finals sidecar exists, queue undelivered finals "
        "from the last N in-game days (default 7).",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    results = []
    for slug in _slugs(args):
        try:
            results.append(
                _notify_league(
                    slug,
                    fallback_days=max(1, int(args.fallback_days)),
                    dry_run=bool(args.dry_run),
                )
            )
        except Exception:
            _log.exception("Discord notify after deploy failed for %s", slug)
            results.append({"league_slug": slug, "error": True})
    failed = sum(1 for r in results if r.get("error"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
