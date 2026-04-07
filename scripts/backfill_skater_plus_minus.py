"""Backfill player_skater_stats.plus_minus from FHM player_skater_stats_*.csv files.

Rows imported before the +/_ column key fix have NULL plus_minus; this updates them
without a full re-import. Safe to run multiple times."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from app.config import Config
from app.models import Player, PlayerSkaterStat, db
from app.services.seasons import get_current_season
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int


def backfill_skater_plus_minus(raw_dir: Path | None = None) -> int:
    """Return number of CSV rows applied (may update same DB row once per segment file)."""
    raw = Path(raw_dir) if raw_dir else Config.RAW_IMPORT_DIR
    season = get_current_season()
    if not season:
        return 0

    fhm_to_pid: dict[int, int] = {}
    for p in db.session.scalars(select(Player)).all():
        if not p.fhm_player_id:
            continue
        try:
            fhm_to_pid[int(str(p.fhm_player_id).strip())] = p.id
        except ValueError:
            continue

    total = 0
    for fname, seg in [
        ("player_skater_stats_rs.csv", "rs"),
        ("player_skater_stats_ps.csv", "ps"),
        ("player_skater_stats_po.csv", "po"),
    ]:
        path = raw / fname
        if not path.exists():
            continue
        df = read_csv_normalized(path)
        for _, row in df.iterrows():
            r = row.to_dict()
            pid_fhm = to_int(cell_val(r, "playerid"))
            if pid_fhm is None or pid_fhm not in fhm_to_pid:
                continue
            pm = to_int(cell_val(r, "+_", "+__", "plus_minus", "pm"))
            row_db = db.session.scalars(
                select(PlayerSkaterStat).where(
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.player_id == fhm_to_pid[pid_fhm],
                    PlayerSkaterStat.stat_segment == seg,
                ).limit(1)
            ).first()
            if row_db is not None:
                row_db.plus_minus = pm
                total += 1
        db.session.commit()
    return total


if __name__ == "__main__":
    from app import create_app

    application = create_app()
    with application.app_context():
        n = backfill_skater_plus_minus()
        print(f"backfill_skater_plus_minus: applied {n} CSV rows")
