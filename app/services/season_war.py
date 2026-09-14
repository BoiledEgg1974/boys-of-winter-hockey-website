"""Permanent per-season WAR register (upsert on import, finalize on rollover)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Player,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSeasonWar,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Season,
)
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.player_career_bowl_lines import dedupe_career_lines_by_season_team_league

_log = logging.getLogger(__name__)

_SEGMENTS = ("rs", "ps", "po")
_FORMULA_RANK = {"full": 2, "legacy": 1}
_MIN_LEGACY_GP = 10
_MIN_LEGACY_GOALIE_GP = 5

_LEGACY_SKATER_WEIGHTS: dict[str, float] = {
    "game_rating": 0.35,
    "pts_per_gp": 0.20,
    "pp_pts_per_gp": 0.15,
    "sh_pts_per_gp": 0.10,
    "finishing": 0.12,
    "plus_minus_per_gp": 0.08,
}

_LEGACY_GOALIE_WEIGHTS: dict[str, float] = {
    "game_rating": 0.40,
    "sv_pct": 0.25,
    "gaa": 0.20,
    "workload": 0.15,
}


def _json_dump(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True, default=str)


def _season_year(season: Season | None) -> int | None:
    if season is None or season.start_year is None:
        return None
    return int(season.start_year)


def war_map_for_season(
    session: Session,
    *,
    season_year: int,
    segment: str = "rs",
) -> dict[tuple[int, bool], PlayerSeasonWar]:
    """Return {(player_id, is_goalie): row} for one season year + segment."""
    rows = session.scalars(
        select(PlayerSeasonWar).where(
            PlayerSeasonWar.season_year == int(season_year),
            PlayerSeasonWar.stat_segment == segment,
        )
    ).all()
    return {(int(r.player_id), bool(r.is_goalie)): r for r in rows}


def war_map_for_player(
    session: Session,
    player_id: int,
    *,
    segment: str = "rs",
) -> dict[int, PlayerSeasonWar]:
    """Return {season_year: row} for one player."""
    rows = session.scalars(
        select(PlayerSeasonWar).where(
            PlayerSeasonWar.player_id == int(player_id),
            PlayerSeasonWar.stat_segment == segment,
            PlayerSeasonWar.is_goalie.is_(False),
        )
    ).all()
    return {int(r.season_year): r for r in rows}


def career_war_summary(rows: list[PlayerSeasonWar], *, is_goalie: bool = False) -> dict[str, int | float | None]:
    """Career average and peak WAR from finalized or any season rows."""
    from app.services.player_percentiles import format_war_wins, war_pct_to_wins

    values = [int(r.war_pct) for r in rows if r.war_pct is not None]
    if not values:
        return {
            "avg_war_pct": None,
            "peak_war_pct": None,
            "avg_war": None,
            "peak_war": None,
            "avg_war_display": "—",
            "peak_war_display": "—",
        }
    avg_pct = int(round(sum(values) / len(values)))
    peak_pct = max(values)
    avg_war = war_pct_to_wins(avg_pct, is_goalie=is_goalie)
    peak_war = war_pct_to_wins(peak_pct, is_goalie=is_goalie)
    return {
        "avg_war_pct": avg_pct,
        "peak_war_pct": peak_pct,
        "avg_war": avg_war,
        "peak_war": peak_war,
        "avg_war_display": format_war_wins(avg_pct, is_goalie=is_goalie),
        "peak_war_display": format_war_wins(peak_pct, is_goalie=is_goalie),
    }


def _should_replace(existing: PlayerSeasonWar | None, *, formula_version: str, is_finalized: bool) -> bool:
    if existing is None:
        return True
    if existing.is_finalized and not is_finalized:
        return False
    if is_finalized and not existing.is_finalized:
        return True
    new_rank = _FORMULA_RANK.get(formula_version, 0)
    old_rank = _FORMULA_RANK.get(str(existing.formula_version or "legacy"), 0)
    if new_rank > old_rank:
        return True
    if new_rank < old_rank:
        return False
    return is_finalized or not existing.is_finalized


def _upsert_row(
    session: Session,
    *,
    league_slug: str,
    player_id: int,
    season_year: int,
    segment: str,
    is_goalie: bool,
    war_pct: int | None,
    formula_version: str,
    gp: int,
    metrics: dict[str, Any],
    is_finalized: bool,
    now: datetime,
) -> bool:
    existing = session.scalars(
        select(PlayerSeasonWar).where(
            PlayerSeasonWar.player_id == int(player_id),
            PlayerSeasonWar.season_year == int(season_year),
            PlayerSeasonWar.stat_segment == segment,
            PlayerSeasonWar.is_goalie == bool(is_goalie),
        ).limit(1)
    ).first()
    if not _should_replace(existing, formula_version=formula_version, is_finalized=is_finalized):
        return False
    if existing is None:
        session.add(
            PlayerSeasonWar(
                player_id=int(player_id),
                league_slug=league_slug,
                season_year=int(season_year),
                stat_segment=segment,
                is_goalie=bool(is_goalie),
                war_pct=war_pct,
                formula_version=formula_version,
                gp=int(gp),
                is_finalized=is_finalized,
                metrics_json=_json_dump(metrics),
                finalized_at=now if is_finalized else None,
                updated_at=now,
            )
        )
        return True
    existing.league_slug = league_slug
    existing.war_pct = war_pct
    existing.formula_version = formula_version
    existing.gp = int(gp)
    existing.metrics_json = _json_dump(metrics)
    existing.updated_at = now
    if is_finalized:
        existing.is_finalized = True
        existing.finalized_at = existing.finalized_at or now
    return True


def _legacy_war_from_weighted_percentiles(
    metrics: dict[str, float | None],
    pools: dict[str, list[float]],
    weights: dict[str, float],
) -> int | None:
    from app.services.player_percentiles import percentile_int

    parts: list[float] = []
    weight_sum = 0.0
    for key, weight in weights.items():
        val = metrics.get(key)
        pool = pools.get(key) or []
        if val is None or len(pool) < 2:
            continue
        higher = key not in ("gaa",)
        pct = percentile_int(val, pool, higher_is_better=higher)
        if pct is None:
            continue
        parts.append(float(pct) * weight)
        weight_sum += weight
    if weight_sum <= 0:
        gr = metrics.get("game_rating")
        return max(0, min(99, int(round(gr)))) if gr is not None else None
    raw = sum(parts) / weight_sum
    return max(0, min(99, int(round(raw))))


def _legacy_skater_metrics(ln: PlayerSkaterCareerLine) -> dict[str, float | None]:
    gp = int(ln.gp or 0)
    if gp <= 0:
        return {}
    goals = int(ln.goals or 0)
    assists = int(ln.assists or 0)
    shots = int(ln.shots or 0)
    pp_pts = int(ln.pp_goals or 0) + int(ln.pp_assists or 0)
    sh_pts = int(ln.sh_goals or 0) + int(ln.sh_assists or 0)
    from app.services.player_percentiles import finishing_value

    finishing = finishing_value(goals, shots)
    pm = float(ln.plus_minus) if ln.plus_minus is not None else None
    return {
        "game_rating": float(ln.game_rating) if ln.game_rating is not None else None,
        "pts_per_gp": (goals + assists) / gp,
        "pp_pts_per_gp": pp_pts / gp,
        "sh_pts_per_gp": sh_pts / gp,
        "finishing": finishing,
        "plus_minus_per_gp": (pm / gp) if pm is not None else None,
    }


def _legacy_goalie_metrics(ln: PlayerGoalieCareerLine) -> dict[str, float | None]:
    gp = int(ln.gp or 0)
    if gp <= 0:
        return {}
    sa = int(ln.shots_against or 0)
    ga = int(ln.goals_against or 0)
    minutes = int(ln.minutes_played or 0)
    sv_pct = ((sa - ga) / sa) if sa > 0 else None
    gaa = ((ga * 60.0) / minutes) if minutes > 0 else None
    return {
        "game_rating": float(ln.game_rating) if ln.game_rating is not None else None,
        "sv_pct": sv_pct,
        "gaa": gaa,
        "workload": float(minutes),
    }


def _legacy_skater_pool(
    lines: list[PlayerSkaterCareerLine],
    *,
    position_by_player: dict[int, str],
) -> dict[str, list[_SkaterPoolRow]]:
    from app.services.player_percentiles import _position_group

    grouped: dict[str, list[_SkaterPoolRow]] = {"forward": [], "defense": []}
    for ln in lines:
        if int(ln.gp or 0) < _MIN_LEGACY_GP:
            continue
        pos = position_by_player.get(int(ln.player_id), "")
        group = _position_group(pos)
        if group == "goalie":
            continue
        metrics = _legacy_skater_metrics(ln)
        if not metrics:
            continue
        grouped.setdefault(group, []).append(_SkaterPoolRow(int(ln.player_id), metrics))
    return grouped


def _legacy_goalie_pool(lines: list[PlayerGoalieCareerLine]) -> list[_GoaliePoolRow]:
    out: list[_GoaliePoolRow] = []
    for ln in lines:
        if int(ln.gp or 0) < _MIN_LEGACY_GOALIE_GP:
            continue
        metrics = _legacy_goalie_metrics(ln)
        if not metrics:
            continue
        out.append(_GoaliePoolRow(int(ln.player_id), metrics))
    return out


class _SkaterPoolRow:
    __slots__ = ("player_id", "metrics")

    def __init__(self, player_id: int, metrics: dict[str, float | None]) -> None:
        self.player_id = player_id
        self.metrics = metrics


class _GoaliePoolRow:
    __slots__ = ("player_id", "metrics")

    def __init__(self, player_id: int, metrics: dict[str, float | None]) -> None:
        self.player_id = player_id
        self.metrics = metrics


def _metric_pools_from_rows(rows: list[Any], keys: set[str]) -> dict[str, list[float]]:
    pools: dict[str, list[float]] = {k: [] for k in keys}
    for row in rows:
        for key in keys:
            val = row.metrics.get(key)
            if val is not None:
                pools[key].append(float(val))
    return pools


def upsert_live_season_war(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    raw_dir: Path,
    is_finalized: bool = False,
) -> int:
    """Compute WAR from live season stats and upsert player_season_war rows."""
    from app.services.player_percentiles import (
        _MIN_PERCENTILE_POOL,
        _build_goalie_pool,
        _build_skater_pool,
        _goalie_metric_pools,
        _goalie_metric_row,
        _goalie_war_pct_from_metrics,
        _load_goalie_ratings_map,
        _metric_pools,
        _position_group,
        _season_game_gr_thresholds,
        _skater_metric_row,
        _war_pct_from_metrics,
    )
    from app.services.advanced_stats import MIN_GOALIE_GP, _adaptive_min_gp, _league_goalie_sv_pct

    year = _season_year(season)
    if year is None:
        return 0
    season_id = int(season.id)
    now = datetime.utcnow()
    updated = 0

    for segment in _SEGMENTS:
        pools_by_group: dict[str, tuple[list[Any], dict[str, list[float]]]] = {}
        stats = session.scalars(
            select(PlayerSkaterStat)
            .options(joinedload(PlayerSkaterStat.player))
            .where(
                PlayerSkaterStat.season_id == season_id,
                PlayerSkaterStat.stat_segment == segment,
            )
        ).all()
        for st in stats:
            player = st.player
            if player is None:
                continue
            pos_group = _position_group(player.position)
            if pos_group not in pools_by_group:
                pool_rows = _build_skater_pool(
                    session, season_id, segment=segment, position_group=pos_group, raw_dir=raw_dir
                )
                pools_by_group[pos_group] = (pool_rows, _metric_pools(pool_rows))
            pool_rows, pools = pools_by_group[pos_group]
            row = _skater_metric_row(session, st, season_id=season_id, segment=segment, raw_dir=raw_dir)
            war_pct = (
                _war_pct_from_metrics(row.metrics, pools)
                if len(pool_rows) >= _MIN_PERCENTILE_POOL
                else None
            )
            if _upsert_row(
                session,
                league_slug=league_slug,
                player_id=int(st.player_id),
                season_year=year,
                segment=segment,
                is_goalie=False,
                war_pct=war_pct,
                formula_version="full",
                gp=int(st.gp or 0),
                metrics={k: v for k, v in row.metrics.items()},
                is_finalized=is_finalized,
                now=now,
            ):
                updated += 1

        min_gp = _adaptive_min_gp(session, PlayerGoalieStat, season_id, segment, MIN_GOALIE_GP)
        g_stats = session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == season_id,
                PlayerGoalieStat.stat_segment == segment,
            )
        ).all()
        if g_stats:
            player_ids = [int(s.player_id) for s in g_stats]
            ratings_by_player = _load_goalie_ratings_map(session, player_ids, None, player_ids[0])
            pool_rows = _build_goalie_pool(
                session,
                season_id,
                segment=segment,
                min_gp=min_gp,
                ratings_by_player=ratings_by_player,
            )
            pools = _goalie_metric_pools(pool_rows)
            league_sv = _league_goalie_sv_pct(
                session.scalars(
                    select(PlayerGoalieStat).where(
                        PlayerGoalieStat.season_id == season_id,
                        PlayerGoalieStat.stat_segment == segment,
                        PlayerGoalieStat.gp >= min_gp,
                    )
                ).all()
            )
            gr_median, gr_p75, gr_p25 = _season_game_gr_thresholds(session, season_id)
            for st in g_stats:
                row = _goalie_metric_row(
                    session,
                    st,
                    season_id=season_id,
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
                if _upsert_row(
                    session,
                    league_slug=league_slug,
                    player_id=int(st.player_id),
                    season_year=year,
                    segment=segment,
                    is_goalie=True,
                    war_pct=war_pct,
                    formula_version="full",
                    gp=int(st.gp or 0),
                    metrics={k: v for k, v in row.metrics.items()},
                    is_finalized=is_finalized,
                    now=now,
                ):
                    updated += 1

    if updated:
        session.commit()
        _log.info(
            "Upserted %s player_season_war row(s) for %s year=%s finalized=%s",
            updated,
            league_slug,
            year,
            is_finalized,
        )
    return updated


def finalize_season_war(
    session: Session,
    *,
    league_slug: str,
    season: Season,
    season_year: int,
    raw_dir: Path,
) -> int:
    """Lock WAR rows for a completed season year before live stats are wiped."""
    n = upsert_live_season_war(
        session,
        league_slug=league_slug,
        season=season,
        raw_dir=raw_dir,
        is_finalized=True,
    )
    now = datetime.utcnow()
    rows = session.scalars(
        select(PlayerSeasonWar).where(
            PlayerSeasonWar.season_year == int(season_year),
            PlayerSeasonWar.is_finalized.is_(False),
        )
    ).all()
    extra = 0
    for row in rows:
        row.is_finalized = True
        row.finalized_at = row.finalized_at or now
        row.updated_at = now
        extra += 1
    if extra:
        session.commit()
        _log.info("Finalized %s additional player_season_war row(s) for year=%s", extra, season_year)
    return n + extra


def backfill_legacy_season_war(
    session: Session,
    *,
    league_slug: str,
    season_year: int,
    segment: str = "rs",
) -> int:
    """Compute Legacy WAR for one season year from career lines (idempotent)."""
    from app.services.player_percentiles import _position_group

    main_league_ids = frozenset(bowl_nhl_league_ids(session))
    if not main_league_ids:
        return 0

    sk_source = {"rs": ("rs", "retired_rs"), "ps": ("ps", "retired_ps"), "po": ("po", "retired_po")}.get(
        segment, ("rs", "retired_rs")
    )
    sk_lines_raw = session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.season_year == int(season_year),
            PlayerSkaterCareerLine.career_source.in_(sk_source),
            PlayerSkaterCareerLine.league_fhm_id.in_(main_league_ids),
        )
    ).all()
    sk_lines = dedupe_career_lines_by_season_team_league(
        list(sk_lines_raw),
        {sk_source[0]: 0, sk_source[1]: 1},
    )

    gk_source = sk_source
    gk_lines_raw = session.scalars(
        select(PlayerGoalieCareerLine).where(
            PlayerGoalieCareerLine.season_year == int(season_year),
            PlayerGoalieCareerLine.career_source.in_(gk_source),
            PlayerGoalieCareerLine.league_fhm_id.in_(main_league_ids),
        )
    ).all()
    gk_lines = dedupe_career_lines_by_season_team_league(
        list(gk_lines_raw),
        {gk_source[0]: 0, gk_source[1]: 1},
    )

    player_ids = {int(ln.player_id) for ln in sk_lines} | {int(ln.player_id) for ln in gk_lines}
    if not player_ids:
        return 0
    players = session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    position_by_player = {int(p.id): str(p.position or "") for p in players}

    now = datetime.utcnow()
    updated = 0
    sk_pools_by_group = _legacy_skater_pool(sk_lines, position_by_player=position_by_player)
    for group, pool_rows in sk_pools_by_group.items():
        if not pool_rows:
            continue
        pools = _metric_pools_from_rows(pool_rows, set(_LEGACY_SKATER_WEIGHTS))
        by_player = {r.player_id: r for r in pool_rows}
        for ln in sk_lines:
            if _position_group(position_by_player.get(int(ln.player_id), "")) != group:
                continue
            if int(ln.player_id) not in by_player:
                continue
            metrics = _legacy_skater_metrics(ln)
            war_pct = _legacy_war_from_weighted_percentiles(metrics, pools, _LEGACY_SKATER_WEIGHTS)
            if _upsert_row(
                session,
                league_slug=league_slug,
                player_id=int(ln.player_id),
                season_year=int(season_year),
                segment=segment,
                is_goalie=False,
                war_pct=war_pct,
                formula_version="legacy",
                gp=int(ln.gp or 0),
                metrics=metrics,
                is_finalized=True,
                now=now,
            ):
                updated += 1

    gk_pool = _legacy_goalie_pool(gk_lines)
    if gk_pool:
        gk_pools = _metric_pools_from_rows(gk_pool, set(_LEGACY_GOALIE_WEIGHTS))
        gk_by_player = {r.player_id: r for r in gk_pool}
        for ln in gk_lines:
            if int(ln.player_id) not in gk_by_player:
                continue
            metrics = _legacy_goalie_metrics(ln)
            war_pct = _legacy_war_from_weighted_percentiles(metrics, gk_pools, _LEGACY_GOALIE_WEIGHTS)
            if _upsert_row(
                session,
                league_slug=league_slug,
                player_id=int(ln.player_id),
                season_year=int(season_year),
                segment=segment,
                is_goalie=True,
                war_pct=war_pct,
                formula_version="legacy",
                gp=int(ln.gp or 0),
                metrics=metrics,
                is_finalized=True,
                now=now,
            ):
                updated += 1

    if updated:
        session.commit()
    return updated


def record_season_war_after_import(app) -> None:
    """Post-import hook: upsert current-season WAR into permanent register."""
    try:
        from pathlib import Path

        from app.config import BASE_DIR, league_raw_import_dir

        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        raw_dir = Path(
            app.config.get("RAW_IMPORT_DIR")
            or (BASE_DIR / "data" / "imports" / "raw" / league_raw_import_dir(slug))
        )
        with app.app_context():
            from app.league_db import db
            from app.services.seasons import get_current_season

            season = get_current_season()
            if season is None:
                return
            upsert_live_season_war(
                db.session,
                league_slug=slug,
                season=season,
                raw_dir=raw_dir,
                is_finalized=False,
            )
    except Exception:
        _log.exception("season WAR upsert failed (non-fatal)")


def build_war_leaders_payload(
    session: Session,
    season: Season,
    segment: str,
    *,
    league_slug: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Top skaters and goalies by WAR for homepage module."""
    from flask import url_for

    from app.services.homepage_leaders import _player_photo_url
    from app.services.player_percentiles import war_pct_to_wins
    from app.services.season_team_logo_bundle import dashboard_team_logo_url

    year = _season_year(season)
    seg = segment if segment in _SEGMENTS else "rs"
    if year is None:
        return {"skaters": [], "goalies": [], "segment": seg, "season_year": None}

    logo_sy = year
    rows = list(
        session.scalars(
            select(PlayerSeasonWar)
            .options(joinedload(PlayerSeasonWar.player).joinedload(Player.current_team))
            .where(
                PlayerSeasonWar.season_year == int(year),
                PlayerSeasonWar.stat_segment == seg,
                PlayerSeasonWar.war_pct.isnot(None),
            )
        ).all()
    )
    needs_refresh = not rows or not any(not r.is_goalie and r.war_pct is not None for r in rows)
    if needs_refresh:
        try:
            from flask import current_app

            from app.config import BASE_DIR, league_raw_import_dir

            raw_dir = Path(
                current_app.config.get("RAW_IMPORT_DIR")
                or (BASE_DIR / "data" / "imports" / "raw" / league_raw_import_dir(league_slug))
            )
            upsert_live_season_war(
                session,
                league_slug=league_slug,
                season=season,
                raw_dir=raw_dir,
                is_finalized=False,
            )
            rows = list(
                session.scalars(
                    select(PlayerSeasonWar)
                    .options(joinedload(PlayerSeasonWar.player).joinedload(Player.current_team))
                    .where(
                        PlayerSeasonWar.season_year == int(year),
                        PlayerSeasonWar.stat_segment == seg,
                        PlayerSeasonWar.war_pct.isnot(None),
                    )
                ).all()
            )
        except Exception:
            _log.exception("WAR leaders live upsert fallback failed")

    def _row_dict(r: PlayerSeasonWar) -> dict[str, Any]:
        pl = r.player
        team = pl.current_team if pl else None
        return {
            "player_id": int(r.player_id),
            "player": pl.full_name if pl else "—",
            "name": pl.full_name if pl else "—",
            "team": team.abbreviation if team else "—",
            "team_slug": team.slug if team else "",
            "team_logo_url": dashboard_team_logo_url(team, logo_sy) if team else "",
            "player_photo_url": _player_photo_url(pl),
            "photo_url": _player_photo_url(pl),
            "player_url": url_for("main.player_page", player_id=int(r.player_id)) if pl else "",
            "gp": int(r.gp or 0),
            "war_pct": int(r.war_pct) if r.war_pct is not None else None,
            "war": war_pct_to_wins(r.war_pct, is_goalie=bool(r.is_goalie)),
            "formula_version": str(r.formula_version or "full"),
        }

    skaters = sorted(
        [_row_dict(r) for r in rows if not r.is_goalie],
        key=lambda x: (x["war_pct"] or -1, x["gp"]),
        reverse=True,
    )[:limit]
    goalies = sorted(
        [_row_dict(r) for r in rows if r.is_goalie],
        key=lambda x: (x["war_pct"] or -1, x["gp"]),
        reverse=True,
    )[:limit]
    return {
        "skaters": skaters,
        "goalies": goalies,
        "segment": seg,
        "season_year": year,
    }
