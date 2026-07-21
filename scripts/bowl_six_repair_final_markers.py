"""Backfill missing BOWL Six game-final markers and re-score affected slates.

Uses the reconstruction JSON produced by
``scripts/bowl_six_reconstruct_final_markers.py`` (real-world first-final
timestamps recovered from schedules.csv git history) to insert markers for
current-season regular-season finals that never got one — the ``deploy-db``
upload path marks games final without a status transition, so production never
recorded them. Existing markers are left untouched.

After inserting markers, every slate whose real-time scoring window gained a
game is re-scored. Already-scored slates also re-sync AP podium awards (prior
payouts are reversed automatically when the podium changes).

Run from the repo root (locally or on PythonAnywhere):

  python scripts/bowl_six_repair_final_markers.py --all
  python scripts/bowl_six_repair_final_markers.py --league bowl-cap --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MAPPING = ROOT / "data" / "imports" / "bowl_six_final_marker_reconstruction.json"
# Finals absent from the reconstruction went final before the walk window; stamp
# them well before any recent slate so they never leak into a current week.
DEFAULT_FALLBACK_TS = "2026-05-01T00:00:00"


def _parse_ts(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(str(raw or "").strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid timestamp {raw!r}; use ISO format") from exc


def _target_slugs(args: argparse.Namespace) -> list[str]:
    from app.config import league_slugs

    if args.all:
        return league_slugs()
    if args.league:
        return [str(args.league).strip()]
    raise SystemExit("Pass --all or --league <slug>.")


def _repair_league(slug: str, mapping: dict[str, str], args: argparse.Namespace) -> None:
    from app.league_db import db
    from app.models import Game
    from app.services.bowl_six import (
        refresh_player_week_stats,
        refresh_slate_lineup_scores,
        slate_real_scoring_window_utc,
        sync_bowl_six_slate_ap_awards,
    )
    from app.services.postseason_odds import _is_regular_season_game
    from app.services.seasons import get_current_season
    from app.site_models import BowlSixGameFinal, BowlSixSlate

    season = get_current_season()
    if season is None:
        print(f"{slug}: no current season; skipping.")
        return

    games = list(
        db.session.scalars(
            select(Game).where(
                Game.season_id == int(season.id),
                Game.status == "final",
            )
        ).all()
    )
    rs_games = [g for g in games if _is_regular_season_game(g.game_type)]
    marked_ids = {
        int(row.game_id)
        for row in db.session.scalars(
            select(BowlSixGameFinal).where(BowlSixGameFinal.league_slug == slug)
        ).all()
    }

    fallback_ts = args.fallback_ts
    inserted: list[tuple[int, datetime]] = []
    unmapped = 0
    for g in rs_games:
        gid = int(g.id)
        if gid in marked_ids:
            continue
        fhm_id = str(g.fhm_game_id or "").strip()
        raw_ts = mapping.get(fhm_id)
        if raw_ts:
            ts = datetime.fromisoformat(raw_ts)
        else:
            ts = fallback_ts
            unmapped += 1
        inserted.append((gid, ts))
        if not args.dry_run:
            db.session.add(
                BowlSixGameFinal(
                    league_slug=slug,
                    game_id=gid,
                    season_id=int(g.season_id) if g.season_id else None,
                    fhm_game_id=fhm_id or None,
                    first_final_at=ts,
                )
            )
    if not args.dry_run and inserted:
        db.session.flush()

    print(
        f"{slug}: {len(rs_games)} final RS game(s), {len(marked_ids)} already marked, "
        f"{len(inserted)} marker(s) {'would be ' if args.dry_run else ''}inserted "
        f"({unmapped} via fallback {fallback_ts.isoformat()})."
    )

    if not inserted:
        if args.dry_run:
            db.session.rollback()
        else:
            db.session.commit()
        return

    inserted_ts = [ts for _gid, ts in inserted]
    slates = list(
        db.session.scalars(
            select(BowlSixSlate)
            .where(
                BowlSixSlate.league_slug == slug,
                BowlSixSlate.status.in_(("open", "locked", "scored")),
            )
            .order_by(BowlSixSlate.week_start)
        ).all()
    )
    for slate in slates:
        window_start, window_end = slate_real_scoring_window_utc(slate)
        gained = sum(1 for ts in inserted_ts if window_start <= ts < window_end)
        if not gained:
            continue
        if args.dry_run:
            print(
                f"[dry-run] {slug}: slate {slate.id} week {slate.week_start} "
                f"({slate.status}) would gain {gained} game(s) and be re-scored."
            )
            continue
        n = refresh_slate_lineup_scores(db.session, db.session, slate)
        refresh_player_week_stats(db.session, slate, db.session)
        if slate.status == "scored":
            slate.scoring_version = int(slate.scoring_version or 0) + 1
            sync_bowl_six_slate_ap_awards(db.session, slate)
        else:
            try:
                from app.services.bowl_six_discord import (
                    maybe_enqueue_bowl_six_leaders_discord,
                )

                maybe_enqueue_bowl_six_leaders_discord(
                    db.session, db.session, slate, force=True
                )
            except Exception:
                print(f"{slug}: Discord leaders enqueue failed for slate {slate.id}.")
        print(
            f"{slug}: slate {slate.id} week {slate.week_start} ({slate.status}) "
            f"gained {gained} game(s); re-scored {n} lineup(s)."
        )

    if args.dry_run:
        db.session.rollback()
    else:
        db.session.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Apply to every configured league slug.")
    group.add_argument("--league", help="Apply to one league slug.")
    ap.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="Reconstruction JSON path.")
    ap.add_argument(
        "--fallback-ts",
        type=_parse_ts,
        default=datetime.fromisoformat(DEFAULT_FALLBACK_TS),
        help="Timestamp for finals missing from the mapping (kept outside recent slate windows).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = ap.parse_args()

    mapping_path = Path(args.mapping)
    if not mapping_path.is_absolute():
        mapping_path = ROOT / mapping_path
    all_mappings: dict[str, dict[str, str]] = json.loads(
        mapping_path.read_text(encoding="utf-8")
    )

    from app import create_app
    from app.config import make_league_config

    for slug in _target_slugs(args):
        os.environ["LEAGUE_SLUG"] = slug
        app = create_app(make_league_config(slug))
        with app.app_context():
            _repair_league(slug, all_mappings.get(slug, {}), args)


if __name__ == "__main__":
    main()
