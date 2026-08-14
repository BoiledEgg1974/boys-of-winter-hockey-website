"""Persist player/team analytics across FHM imports for progression charts."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    AdvancedStatsHubSnapshot,
    PlayerAnalyticsSnapshot,
    PlayerGoalieStat,
    PlayerSkaterStat,
    Season,
    TeamAnalyticsSnapshot,
    TeamStanding,
    db,
)

_log = logging.getLogger(__name__)

_SEGMENTS = ("rs", "ps", "po")

_SKATER_PCT_KEYS = (
    "game_rating_off",
    "game_rating_def",
    "finishing",
    "pp_pts_per_60",
    "sh_pts_per_60",
)
_GOALIE_PCT_KEYS = ("sv_pct", "gsaa", "gaa", "game_rating", "consistency")


def _json_dump(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True, default=str)


def _json_load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _season_year(season: Season | None) -> int | None:
    if season is None or season.start_year is None:
        return None
    return int(season.start_year)


def _snapshot_skater_segment(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    season_year: int,
    segment: str,
    raw_dir: Path,
    is_rollover: bool,
    snapshot_at: datetime,
) -> int:
    from app.services.player_percentiles import (
        _MIN_PERCENTILE_POOL,
        _build_skater_pool,
        _metric_pools,
        _position_group,
        _skater_metric_row,
        _war_pct_from_metrics,
        percentile_int,
    )

    stats = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
        .where(
            PlayerSkaterStat.season_id == int(season.id),
            PlayerSkaterStat.stat_segment == segment,
        )
    ).all()
    if not stats:
        return 0

    pools_by_group: dict[str, tuple[list[Any], dict[str, list[float]]]] = {}
    added = 0
    for st in stats:
        player = st.player
        if player is None:
            continue
        pos_group = _position_group(player.position)
        if pos_group not in pools_by_group:
            pool_rows = _build_skater_pool(
                session,
                int(season.id),
                segment=segment,
                position_group=pos_group,
                raw_dir=raw_dir,
            )
            pools_by_group[pos_group] = (pool_rows, _metric_pools(pool_rows))
        pool_rows, pools = pools_by_group[pos_group]
        row = _skater_metric_row(session, st, season_id=int(season.id), segment=segment, raw_dir=raw_dir)
        war_pct = (
            _war_pct_from_metrics(row.metrics, pools)
            if len(pool_rows) >= _MIN_PERCENTILE_POOL
            else None
        )
        pct = {
            key: percentile_int(
                row.metrics.get(key),
                pools.get(key) or [],
                higher_is_better=key not in ("ga_per_60", "pim", "pim_per_60"),
            )
            for key in _SKATER_PCT_KEYS
        }
        session.add(
            PlayerAnalyticsSnapshot(
                player_id=int(st.player_id),
                league_slug=league_slug,
                season_year=season_year,
                stat_segment=segment,
                is_goalie=False,
                is_rollover=is_rollover,
                snapshot_at=snapshot_at,
                war_pct=war_pct,
                gp=int(st.gp or 0),
                metrics_json=_json_dump({k: v for k, v in row.metrics.items()}),
                percentiles_json=_json_dump(pct),
            )
        )
        added += 1
    return added


def _snapshot_goalie_segment(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    season_year: int,
    segment: str,
    is_rollover: bool,
    snapshot_at: datetime,
) -> int:
    from app.services.advanced_stats import MIN_GOALIE_GP, _adaptive_min_gp, _league_goalie_sv_pct
    from app.services.player_percentiles import (
        _MIN_PERCENTILE_POOL,
        _build_goalie_pool,
        _goalie_metric_pools,
        _goalie_metric_row,
        _goalie_war_pct_from_metrics,
        _load_goalie_ratings_map,
        _season_game_gr_thresholds,
        percentile_int,
    )

    stats = session.scalars(
        select(PlayerGoalieStat)
        .options(joinedload(PlayerGoalieStat.player), joinedload(PlayerGoalieStat.team))
        .where(
            PlayerGoalieStat.season_id == int(season.id),
            PlayerGoalieStat.stat_segment == segment,
        )
    ).all()
    if not stats:
        return 0

    min_gp = _adaptive_min_gp(session, PlayerGoalieStat, int(season.id), segment, MIN_GOALIE_GP)
    player_ids = [int(s.player_id) for s in stats]
    ratings_by_player = _load_goalie_ratings_map(session, player_ids, None, player_ids[0])
    pool_rows = _build_goalie_pool(
        session,
        int(season.id),
        segment=segment,
        min_gp=min_gp,
        ratings_by_player=ratings_by_player,
    )
    pools = _goalie_metric_pools(pool_rows)
    league_sv = _league_goalie_sv_pct(
        session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == int(season.id),
                PlayerGoalieStat.stat_segment == segment,
                PlayerGoalieStat.gp >= min_gp,
            )
        ).all()
    )
    gr_median, gr_p75, gr_p25 = _season_game_gr_thresholds(session, int(season.id))
    added = 0
    for st in stats:
        row = _goalie_metric_row(
            session,
            st,
            season_id=int(season.id),
            league_sv_pct=league_sv,
            gr_median=gr_median,
            gr_p75=gr_p75,
            gr_p25=gr_p25,
            ratings_row=ratings_by_player.get(int(st.player_id)),
        )
        war_pct = (
            _goalie_war_pct_from_metrics(row.metrics, pools)
            if len(pool_rows) >= _MIN_PERCENTILE_POOL
            else None
        )
        pct = {
            key: percentile_int(
                row.metrics.get(key),
                pools.get(key) or [],
                higher_is_better=key != "gaa",
            )
            for key in _GOALIE_PCT_KEYS
        }
        session.add(
            PlayerAnalyticsSnapshot(
                player_id=int(st.player_id),
                league_slug=league_slug,
                season_year=season_year,
                stat_segment=segment,
                is_goalie=True,
                is_rollover=is_rollover,
                snapshot_at=snapshot_at,
                war_pct=war_pct,
                gp=int(st.gp or 0),
                metrics_json=_json_dump({k: v for k, v in row.metrics.items()}),
                percentiles_json=_json_dump(pct),
            )
        )
        added += 1
    return added


def _snapshot_team_segments(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    season_year: int,
    is_rollover: bool,
    snapshot_at: datetime,
) -> int:
    from app.services.advanced_stats import _team_chart_metric_values, build_team_process_rows

    standings = {
        int(s.team_id): s
        for s in session.scalars(select(TeamStanding).where(TeamStanding.season_id == int(season.id))).all()
    }
    added = 0
    for segment in _SEGMENTS:
        rows = build_team_process_rows(session, int(season.id), segment=segment)
        for row in rows:
            st = standings.get(int(row["team_id"]))
            enriched = {
                **row,
                "pts": int(st.pts) if st and st.pts is not None else None,
                "gp": int(st.gp or 0) if st else row.get("gp"),
            }
            metrics = _team_chart_metric_values(enriched)
            if not any(v is not None for k, v in metrics.items() if k != "gp"):
                continue
            session.add(
                TeamAnalyticsSnapshot(
                    team_id=int(row["team_id"]),
                    league_slug=league_slug,
                    season_year=season_year,
                    stat_segment=segment,
                    is_rollover=is_rollover,
                    snapshot_at=snapshot_at,
                    metrics_json=_json_dump({k: v for k, v in metrics.items()}),
                )
            )
            added += 1
    return added


def record_analytics_snapshots_for_league(
    session: Session,
    league_slug: str,
    *,
    raw_dir: Path,
    season: Season | None = None,
    season_year: int | None = None,
    is_rollover: bool = False,
) -> dict[str, int]:
    """Append player + team analytics snapshots from current live season tables."""
    from app.services.seasons import get_current_season

    slug = (league_slug or "").strip()
    if not slug:
        return {"players": 0, "teams": 0}
    season = season or get_current_season()
    if season is None:
        return {"players": 0, "teams": 0}
    year = season_year if season_year is not None else _season_year(season)
    if year is None:
        return {"players": 0, "teams": 0}

    snapshot_at = datetime.utcnow()
    players = 0
    for segment in _SEGMENTS:
        players += _snapshot_skater_segment(
            session,
            league_slug=slug,
            season=season,
            season_year=year,
            segment=segment,
            raw_dir=raw_dir,
            is_rollover=is_rollover,
            snapshot_at=snapshot_at,
        )
        players += _snapshot_goalie_segment(
            session,
            league_slug=slug,
            season=season,
            season_year=year,
            segment=segment,
            is_rollover=is_rollover,
            snapshot_at=snapshot_at,
        )
    teams = _snapshot_team_segments(
        session,
        league_slug=slug,
        season=season,
        season_year=year,
        is_rollover=is_rollover,
        snapshot_at=snapshot_at,
    )
    hubs = _snapshot_advanced_stats_hub_segments(
        session,
        league_slug=slug,
        season=season,
        season_year=year,
        is_rollover=is_rollover,
        snapshot_at=snapshot_at,
    )
    if players or teams or hubs:
        session.commit()
        _log.info(
            "Recorded analytics snapshots for %s (players=%s teams=%s hubs=%s rollover=%s year=%s).",
            slug,
            players,
            teams,
            hubs,
            is_rollover,
            year,
        )
    return {"players": players, "teams": teams, "hubs": hubs}


def seed_analytics_snapshots_if_empty(
    session: Session,
    league_slug: str,
    *,
    raw_dir: Path,
    season: Season | None = None,
) -> dict[str, int]:
    """Seed one baseline snapshot when history tables are empty."""
    player_n = session.scalar(select(func.count()).select_from(PlayerAnalyticsSnapshot)) or 0
    team_n = session.scalar(select(func.count()).select_from(TeamAnalyticsSnapshot)) or 0
    hub_n = session.scalar(select(func.count()).select_from(AdvancedStatsHubSnapshot)) or 0
    if player_n > 0 or team_n > 0 or hub_n > 0:
        return {"players": 0, "teams": 0, "hubs": 0}
    return record_analytics_snapshots_for_league(
        session,
        league_slug,
        raw_dir=raw_dir,
        season=season,
        is_rollover=False,
    )


def load_player_analytics_snapshots(
    session: Session,
    player_id: int,
    *,
    segment: str = "rs",
    is_goalie: bool | None = None,
) -> list[PlayerAnalyticsSnapshot]:
    q = (
        select(PlayerAnalyticsSnapshot)
        .where(
            PlayerAnalyticsSnapshot.player_id == int(player_id),
            PlayerAnalyticsSnapshot.stat_segment == segment,
        )
        .order_by(PlayerAnalyticsSnapshot.snapshot_at.asc(), PlayerAnalyticsSnapshot.id.asc())
    )
    if is_goalie is not None:
        q = q.where(PlayerAnalyticsSnapshot.is_goalie == bool(is_goalie))
    return list(session.scalars(q).all())


def player_snapshot_trend_series(
    snapshots: list[PlayerAnalyticsSnapshot],
    *,
    is_goalie: bool,
) -> dict[str, Any]:
    """Build chart-ready labels/series from stored analytics snapshots."""
    labels: list[str] = []
    war_series: list[int | None] = []
    off_series: list[int | None] = []
    def_series: list[int | None] = []
    fin_series: list[int | None] = []
    sv_series: list[float | None] = []
    league_sv_series: list[float | None] = []

    for snap in snapshots:
        if snap.is_rollover:
            year = int(snap.season_year)
            label = f"{year % 100:02d}-{(year + 1) % 100:02d}"
        else:
            label = snap.snapshot_at.strftime("%m/%d") if snap.snapshot_at else str(snap.season_year)
        labels.append(label)
        war_series.append(int(snap.war_pct) if snap.war_pct is not None else None)
        pct = _json_load(snap.percentiles_json)
        metrics = _json_load(snap.metrics_json)
        if is_goalie:
            sv = metrics.get("sv_pct")
            sv_series.append(round(float(sv) * 100.0, 1) if sv is not None else None)
            league_sv_series.append(None)
            off_series.append(pct.get("sv_pct"))
            def_series.append(pct.get("gsaa"))
            fin_series.append(pct.get("consistency"))
        else:
            off_series.append(pct.get("game_rating_off"))
            def_series.append(pct.get("game_rating_def"))
            fin_series.append(pct.get("finishing"))

    return {
        "labels": labels,
        "war": war_series,
        "off": off_series,
        "def": def_series,
        "fin": fin_series,
        "sv": sv_series,
        "league_sv": league_sv_series,
    }


def load_team_rollover_years(session: Session) -> list[int]:
    """Distinct league start years that have a finalized (rollover) team snapshot."""
    years = session.scalars(
        select(TeamAnalyticsSnapshot.season_year)
        .where(TeamAnalyticsSnapshot.is_rollover.is_(True))
        .distinct()
        .order_by(TeamAnalyticsSnapshot.season_year.asc())
    ).all()
    return [int(y) for y in years if y is not None]


def latest_team_rollover_rows(
    session: Session,
    *,
    season_year: int,
    segment: str,
) -> list[TeamAnalyticsSnapshot]:
    """Latest rollover snapshot per team for a given season year + segment."""
    rows = session.scalars(
        select(TeamAnalyticsSnapshot)
        .options(joinedload(TeamAnalyticsSnapshot.team))
        .where(
            TeamAnalyticsSnapshot.season_year == int(season_year),
            TeamAnalyticsSnapshot.stat_segment == segment,
            TeamAnalyticsSnapshot.is_rollover.is_(True),
        )
        .order_by(TeamAnalyticsSnapshot.snapshot_at.desc(), TeamAnalyticsSnapshot.id.desc())
    ).all()
    by_team: dict[int, TeamAnalyticsSnapshot] = {}
    for row in rows:
        tid = int(row.team_id)
        if tid not in by_team:
            by_team[tid] = row
    return list(by_team.values())


def _snapshot_advanced_stats_hub_segments(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    season_year: int,
    is_rollover: bool,
    snapshot_at: datetime,
) -> int:
    """Persist Advanced Stats hub JSON per segment (includes lines + shot quality)."""
    from app.services.advanced_stats import build_advanced_stats_hub_json

    added = 0
    for segment in _SEGMENTS:
        try:
            hub = build_advanced_stats_hub_json(session, int(season.id), segment=segment)
        except Exception:
            _log.exception(
                "Failed building advanced stats hub snapshot for %s year=%s segment=%s",
                league_slug,
                season_year,
                segment,
            )
            continue
        # Skip empty hubs (no leaderboard rows and no lines).
        has_rows = any(
            hub.get(key)
            for key in ("skaters", "goalies", "teams", "luck", "discipline", "lines", "shot_quality")
        )
        if not has_rows:
            continue
        session.add(
            AdvancedStatsHubSnapshot(
                league_slug=league_slug,
                season_year=int(season_year),
                stat_segment=segment,
                is_rollover=is_rollover,
                snapshot_at=snapshot_at,
                hub_json=_json_dump(hub),
            )
        )
        added += 1
    return added


def load_hub_rollover_years(session: Session) -> list[int]:
    """Distinct league start years that have a finalized (rollover) Advanced Stats hub snapshot."""
    years = session.scalars(
        select(AdvancedStatsHubSnapshot.season_year)
        .where(AdvancedStatsHubSnapshot.is_rollover.is_(True))
        .distinct()
        .order_by(AdvancedStatsHubSnapshot.season_year.asc())
    ).all()
    return [int(y) for y in years if y is not None]


def latest_hub_rollover_snapshot(
    session: Session,
    *,
    season_year: int,
    segment: str,
) -> AdvancedStatsHubSnapshot | None:
    """Latest rollover Advanced Stats hub snapshot for a season year + segment."""
    return session.scalars(
        select(AdvancedStatsHubSnapshot)
        .where(
            AdvancedStatsHubSnapshot.season_year == int(season_year),
            AdvancedStatsHubSnapshot.stat_segment == segment,
            AdvancedStatsHubSnapshot.is_rollover.is_(True),
        )
        .order_by(AdvancedStatsHubSnapshot.snapshot_at.desc(), AdvancedStatsHubSnapshot.id.desc())
        .limit(1)
    ).first()


def archive_analytics_before_fhm_wipe(
    session: Session,
    league_slug: str,
    *,
    raw_dir: Path,
    season: Season,
    season_year: int,
) -> dict[str, int]:
    """Archive live analytics before season stats are cleared (season-year rollover)."""
    return record_analytics_snapshots_for_league(
        session,
        league_slug,
        raw_dir=raw_dir,
        season=season,
        season_year=season_year,
        is_rollover=True,
    )


def record_analytics_snapshots_after_import(app) -> None:
    """Post-import hook: capture current analytics for progression charts."""
    try:
        from app.config import BASE_DIR, league_raw_import_dir

        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        raw_dir = Path(app.config.get("RAW_IMPORT_DIR") or (BASE_DIR / "data" / "imports" / "raw" / league_raw_import_dir(slug)))
        with app.app_context():
            from app.services.seasons import get_current_season

            season = get_current_season()
            seeded = seed_analytics_snapshots_if_empty(
                db.session, slug, raw_dir=raw_dir, season=season
            )
            if seeded["players"] or seeded["teams"] or seeded.get("hubs"):
                return
            # Always append a fresh post-import point (keep every import).
            record_analytics_snapshots_for_league(
                db.session,
                slug,
                raw_dir=raw_dir,
                season=season,
                is_rollover=False,
            )
    except Exception:
        _log.exception("analytics snapshot capture failed (non-fatal)")
