"""FHM season stat rows vs what the site statistics tables show (BOWL tier leagues)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PlayerSkaterStat, Team
from app.services.all_time_records import bowl_nhl_league_ids
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int
from scripts.import_pipeline.fhm_loader import relegation_tier_league_ids, team_data_csv_path


def tier_fhm_team_ids_from_team_data(raw_dir: Path) -> frozenset[int]:
    """FHM ``TeamId`` values for BOWL-Upper / BOWL-Lower teams in ``team_data.csv``."""
    path = team_data_csv_path(raw_dir)
    if path is None:
        return frozenset()
    tier_leagues = relegation_tier_league_ids(raw_dir)
    if not tier_leagues:
        return frozenset()
    allowed = frozenset(int(x) for x in tier_leagues)
    out: set[int] = set()
    df = read_csv_normalized(path)
    for _, row in df.iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "leagueid", "league_id"))
        tid = to_int(cell_val(r, "teamid", "team_id"))
        if lid is None or tid is None or int(lid) not in allowed:
            continue
        out.add(int(tid))
    return frozenset(out)


def rs_skater_csv_has_tier_team_rows(raw_dir: Path) -> bool:
    """True if ``player_skater_stats_rs.csv`` includes at least one BOWL tier team row."""
    path = raw_dir / "player_skater_stats_rs.csv"
    if not path.is_file():
        return False
    tier_teams = tier_fhm_team_ids_from_team_data(raw_dir)
    if not tier_teams:
        return True
    df = read_csv_normalized(path)
    for _, row in df.iterrows():
        tid = to_int(cell_val(row.to_dict(), "teamid", "team_id"))
        if tid is not None and int(tid) in tier_teams:
            return True
    return False


def count_site_visible_rs_skater_stats(session: Session, season_id: int, *, league_slug: str) -> int:
    """Match ``/statistics`` RS skater query (main BOWL leagues only for multi-league sites)."""
    q = (
        select(func.count())
        .select_from(PlayerSkaterStat)
        .where(
            PlayerSkaterStat.season_id == int(season_id),
            PlayerSkaterStat.stat_segment == "rs",
        )
    )
    if league_slug in ("bowl-fantasy", "bowl-historical", "bowl-cap"):
        bowl_ids = bowl_nhl_league_ids(session)
        if not bowl_ids:
            bowl_ids = (0,)
        q = q.join(Team, PlayerSkaterStat.team_id == Team.id).where(
            Team.fhm_league_id.in_(bowl_ids)
        )
    return int(session.scalar(q) or 0)
