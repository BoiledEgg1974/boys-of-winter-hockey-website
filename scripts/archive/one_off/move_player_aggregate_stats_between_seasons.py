"""
Re-point season-level player aggregates (skater + goalie) from one Season row to another.

Use when standings / ``is_current`` already reflect the new year (e.g. 1968-69) but FHM
imports left ``player_skater_stats`` / ``player_goalie_stats`` rows on the previous
season_id after a rollover.

From project root (set LEAGUE_SLUG / config the same way as other league scripts):

  python scripts/move_player_aggregate_stats_between_seasons.py --dry-run
  python scripts/move_player_aggregate_stats_between_seasons.py --execute

Optional explicit seasons (labels must match ``seasons.label`` in the DB):

  python scripts/move_player_aggregate_stats_between_seasons.py \\
      --from-label \"1967-1968\" --to-label \"1968-1969\" --execute

If ``--from-label`` is omitted, the script picks the non-target season with the most
regular-season skater stat rows (excluding the target season).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.models import PlayerGoalieStat, PlayerSkaterStat, Season, db  # noqa: E402


def _resolve_season(label: str | None) -> Season | None:
    if not label or not label.strip():
        return None
    lab = label.strip()
    s = db.session.scalar(select(Season).where(Season.label == lab).limit(1))
    if s:
        return s
    low = lab.lower()
    for row in db.session.scalars(select(Season)).all():
        if (row.label or "").strip().lower() == low:
            return row
    return None


def _pick_source_season_id(exclude_id: int) -> int | None:
    """Season id (other than exclude) with the most RS skater stat rows."""
    sid = db.session.scalar(
        select(PlayerSkaterStat.season_id, func.count(PlayerSkaterStat.id).label("n"))
        .where(
            PlayerSkaterStat.season_id != exclude_id,
            PlayerSkaterStat.stat_segment == "rs",
        )
        .group_by(PlayerSkaterStat.season_id)
        .order_by(func.count(PlayerSkaterStat.id).desc(), PlayerSkaterStat.season_id.desc())
        .limit(1)
    )
    return int(sid) if sid is not None else None


def _conflicting_skater_ids(target_id: int, source_id: int) -> list[tuple[int, str]]:
    q = (
        select(PlayerSkaterStat.player_id, PlayerSkaterStat.stat_segment)
        .where(PlayerSkaterStat.season_id == source_id)
        .intersect(
            select(PlayerSkaterStat.player_id, PlayerSkaterStat.stat_segment).where(
                PlayerSkaterStat.season_id == target_id
            )
        )
    )
    return [(int(r[0]), str(r[1])) for r in db.session.execute(q)]


def _conflicting_goalie_ids(target_id: int, source_id: int) -> list[tuple[int, str]]:
    q = (
        select(PlayerGoalieStat.player_id, PlayerGoalieStat.stat_segment)
        .where(PlayerGoalieStat.season_id == source_id)
        .intersect(
            select(PlayerGoalieStat.player_id, PlayerGoalieStat.stat_segment).where(
                PlayerGoalieStat.season_id == target_id
            )
        )
    )
    return [(int(r[0]), str(r[1])) for r in db.session.execute(q)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--to-label",
        default=None,
        help="Target season label (default: the season with is_current=True)",
    )
    p.add_argument(
        "--from-label",
        default=None,
        help="Source season label (default: season with most RS skater rows other than target)",
    )
    p.add_argument("--execute", action="store_true", help="Apply changes (default is dry-run)")
    args = p.parse_args()

    app = create_app()
    with app.app_context():
        target: Season | None = None
        if args.to_label:
            target = _resolve_season(args.to_label)
            if not target:
                print(f"No season found with label matching {args.to_label!r}.")
                sys.exit(1)
        else:
            target = db.session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
            if not target:
                print("No is_current season; pass --to-label explicitly.")
                sys.exit(1)

        source: Season | None = None
        if args.from_label:
            source = _resolve_season(args.from_label)
            if not source:
                print(f"No season found with label matching {args.from_label!r}.")
                sys.exit(1)
        else:
            sid = _pick_source_season_id(int(target.id))
            if sid is None:
                print("Could not infer a source season (no skater RS stats outside target).")
                sys.exit(1)
            source = db.session.get(Season, sid)

        if source is None or int(source.id) == int(target.id):
            print("Source and target seasons must differ.")
            sys.exit(1)

        n_sk = int(
            db.session.scalar(
                select(func.count()).select_from(PlayerSkaterStat).where(
                    PlayerSkaterStat.season_id == source.id
                )
            )
            or 0
        )
        n_gk = int(
            db.session.scalar(
                select(func.count()).select_from(PlayerGoalieStat).where(
                    PlayerGoalieStat.season_id == source.id
                )
            )
            or 0
        )

        sk_conf = _conflicting_skater_ids(int(target.id), int(source.id))
        gk_conf = _conflicting_goalie_ids(int(target.id), int(source.id))

        print(f"Target season: id={target.id} label={target.label!r} (is_current={target.is_current})")
        print(f"Source season: id={source.id} label={source.label!r}")
        print(f"Rows to move: player_skater_stats={n_sk} player_goalie_stats={n_gk}")
        if sk_conf:
            print(f"WARNING: {len(sk_conf)} skater (player, segment) pairs already exist on target; those rows will be skipped.")
        if gk_conf:
            print(f"WARNING: {len(gk_conf)} goalie (player, segment) pairs already exist on target; those rows will be skipped.")

        if not args.execute:
            print("Dry-run only. Re-run with --execute to apply.")
            return

        if sk_conf:
            bad = {f"{a}:{b}" for a, b in sk_conf}
            for row in db.session.scalars(
                select(PlayerSkaterStat).where(PlayerSkaterStat.season_id == source.id)
            ).all():
                key = f"{row.player_id}:{row.stat_segment}"
                if key in bad:
                    continue
                row.season_id = int(target.id)

        else:
            for row in db.session.scalars(
                select(PlayerSkaterStat).where(PlayerSkaterStat.season_id == source.id)
            ).all():
                row.season_id = int(target.id)

        if gk_conf:
            bad_g = {f"{a}:{b}" for a, b in gk_conf}
            for row in db.session.scalars(
                select(PlayerGoalieStat).where(PlayerGoalieStat.season_id == source.id)
            ).all():
                key = f"{row.player_id}:{row.stat_segment}"
                if key in bad_g:
                    continue
                row.season_id = int(target.id)
        else:
            for row in db.session.scalars(
                select(PlayerGoalieStat).where(PlayerGoalieStat.season_id == source.id)
            ).all():
                row.season_id = int(target.id)

        db.session.commit()
        print("Done. Player aggregate stats now use the target season_id.")


if __name__ == "__main__":
    main()
