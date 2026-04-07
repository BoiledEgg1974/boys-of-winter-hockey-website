"""
Re-import only team_stats.csv and team_stats_playoffs.csv into TeamSeasonAggregate.

Use when full FHM import fails partway, or after adding columns (e.g. SH Ch), without re-running the entire bundle.

From project root:
  python scripts/refresh_team_aggregates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.models import Season, db  # noqa: E402
from app.services.seasons import get_current_season  # noqa: E402
from scripts.import_pipeline.fhm_loader import (  # noqa: E402
    import_team_season_stats,
    import_fhm_teams,
    load_division_names,
)


def main() -> None:
    app = create_app()
    raw = Path(app.config["RAW_IMPORT_DIR"])
    if not (raw / "team_data.csv").is_file():
        print("Expected FHM bundle: team_data.csv not found in", raw)
        sys.exit(1)
    with app.app_context():
        season = get_current_season()
        if not season:
            print("No current season in database. Run a full import first.")
            sys.exit(1)
        league_filter = 0
        div_map = load_division_names(raw)
        teams_fhm = import_fhm_teams(raw, league_filter, div_map)
        n_rs = import_team_season_stats(
            raw, season, teams_fhm, league_filter, filename="team_stats.csv", stat_segment="rs"
        )
        n_po = import_team_season_stats(
            raw,
            season,
            teams_fhm,
            league_filter,
            filename="team_stats_playoffs.csv",
            stat_segment="po",
        )
        db.session.commit()
        print(f"team_stats.csv (rs): {n_rs} rows")
        print(f"team_stats_playoffs.csv (po): {n_po} rows")


if __name__ == "__main__":
    main()
