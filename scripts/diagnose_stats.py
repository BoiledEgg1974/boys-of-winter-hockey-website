"""Print which DB and season the statistics page uses (catch wrong LEAGUE_SLUG / DB pair).

Usage:
  python scripts/diagnose_stats.py bowl-cap
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.config import make_league_config  # noqa: E402
from app.models import Player, PlayerSkaterStat, Season, db  # noqa: E402
from app.services.seasons import get_current_season, season_display_label  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "league",
        nargs="?",
        default=os.environ.get("LEAGUE_SLUG", ""),
        help="bowl-cap | bowl-fantasy | bowl-historical",
    )
    args = p.parse_args()
    slug = (args.league or "").strip()
    if not slug:
        print("Example: python scripts/diagnose_stats.py bowl-cap", file=sys.stderr)
        sys.exit(1)
    os.environ["LEAGUE_SLUG"] = slug
    app = create_app(make_league_config(slug))
    raw = Path(app.config["RAW_IMPORT_DIR"])
    from scripts.import_pipeline.fhm_loader import is_fhm_export_dir  # noqa: E402

    print("LEAGUE_SLUG:", slug)
    print("SQLALCHEMY_DATABASE_URI:", app.config.get("SQLALCHEMY_DATABASE_URI"))
    print("RAW_IMPORT_DIR:", raw.resolve())
    print("FHM semicolon bundle (team_data.csv):", bool(is_fhm_export_dir(raw)))
    sk = raw / "player_skater_stats_rs.csv"
    print("player_skater_stats_rs.csv exists:", sk.is_file(), f"({sk})" if sk.is_file() else "")

    with app.app_context():
        seasons = db.session.scalars(select(Season).order_by(Season.id)).all()
        print("Season rows:", [(s.id, s.label, s.fhm_season_id, s.is_current, s.start_year) for s in seasons])
        cur = get_current_season()
        print(
            "get_current_season():",
            None if cur is None else f"id={cur.id} label={season_display_label(cur)!r} fhm={cur.fhm_season_id!r} is_current={cur.is_current}",
        )
        if cur:
            by_sid = db.session.execute(
                select(PlayerSkaterStat.season_id, func.count()).group_by(PlayerSkaterStat.season_id)
            ).all()
            print("PlayerSkaterStat row counts by season_id:", list(by_sid))
            top = db.session.execute(
                select(Player.full_name, PlayerSkaterStat.gp, PlayerSkaterStat.points)
                .join(Player, PlayerSkaterStat.player_id == Player.id)
                .where(PlayerSkaterStat.season_id == cur.id, PlayerSkaterStat.stat_segment == "rs")
                .order_by(PlayerSkaterStat.points.desc())
                .limit(5)
            ).all()
            print("Top 5 RS skaters (points) for current season_id:", list(top))


if __name__ == "__main__":
    main()
