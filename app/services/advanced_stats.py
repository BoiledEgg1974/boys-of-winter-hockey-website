"""Process-over-results analytics from imported FHM stats (not xG)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    PenaltyEvent,
    Player,
    PlayerGoalieStat,
    PlayerSkaterStat,
    ScoringEvent,
    Season,
    Team,
    TeamSeasonAggregate,
    TeamStanding,
)

MIN_SKATER_GP = 10
MIN_SKATER_TOI_SECONDS = 600 * 60
MIN_GOALIE_GP = 5
SQ_LABELS = ("SQ0", "SQ1", "SQ2", "SQ3", "SQ4")
SQ_KEYS = ("sq0", "sq1", "sq2", "sq3", "sq4")


def _adaptive_min_gp(session: Session, model: Any, season_id: int, segment: str, default: int) -> int:
    max_gp = session.scalar(
        select(func.max(model.gp)).where(model.season_id == season_id, model.stat_segment == segment)
    )
    if max_gp is None:
        return default
    return max(1, min(default, int(max_gp) // 4 or 1))


def _has_full_toi_sample(session: Session, season_id: int, segment: str) -> bool:
    max_toi = session.scalar(
        select(func.max(PlayerSkaterStat.toi_seconds)).where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
        )
    )
    return int(max_toi or 0) >= MIN_SKATER_TOI_SECONDS


def zone_start_pcts(oz: int | None, nz: int | None, dz: int | None) -> dict[str, float | None]:
    total = int(oz or 0) + int(nz or 0) + int(dz or 0)
    if total <= 0:
        return {"oz": None, "nz": None, "dz": None}
    return {
        "oz": round(100.0 * int(oz or 0) / total, 1),
        "nz": round(100.0 * int(nz or 0) / total, 1),
        "dz": round(100.0 * int(dz or 0) / total, 1),
    }


def sq_profile_from_counts(counts: dict[str, int]) -> dict[str, Any]:
    total = sum(int(counts.get(k, 0) or 0) for k in SQ_KEYS)
    shares: dict[str, float | None] = {}
    for key, label in zip(SQ_KEYS, SQ_LABELS):
        v = int(counts.get(key, 0) or 0)
        shares[label] = round(100.0 * v / total, 1) if total > 0 else None
    high = int(counts.get("sq3", 0) or 0) + int(counts.get("sq4", 0) or 0)
    return {
        "counts": {label: int(counts.get(k, 0) or 0) for k, label in zip(SQ_KEYS, SQ_LABELS)},
        "shares": shares,
        "total": total,
        "high_danger_share": round(100.0 * high / total, 1) if total > 0 else None,
    }


def pdo_band(pdo: float | None) -> str | None:
    if pdo is None:
        return None
    if pdo > 101.0:
        return "hot"
    if pdo < 99.0:
        return "cold"
    return "neutral"


def _pp_pct(pp_goals: int | None, pp_chances: int | None) -> float | None:
    if pp_chances is None or pp_chances <= 0:
        return None
    return round(100.0 * float(pp_goals or 0) / float(pp_chances), 1)


def _pk_pct(pk_ga: int | None, sh_chances: int | None) -> float | None:
    if sh_chances is None or sh_chances <= 0:
        return None
    return round((1.0 - float(pk_ga or 0) / float(sh_chances)) * 100.0, 1)


def _player_pts_per_60(st: PlayerSkaterStat) -> float | None:
    if not st.toi_seconds or st.toi_seconds <= 0:
        return None
    pts = float(st.points or 0)
    return round(pts / (st.toi_seconds / 3600.0), 2)


def _player_pp_pts_per_60(st: PlayerSkaterStat) -> float | None:
    if not st.ppto_seconds or st.ppto_seconds <= 0:
        return None
    pts = float((st.ppg or 0) + (st.pp_assists or 0))
    return round(pts / (st.ppto_seconds / 3600.0), 2)


def _player_sh_pts_per_60(st: PlayerSkaterStat) -> float | None:
    if not st.shto_seconds or st.shto_seconds <= 0:
        return None
    pts = float((st.shg or 0) + (st.sh_assists or 0))
    return round(pts / (st.shto_seconds / 3600.0), 2)


def _pct_from_pair(for_count: int | None, against_count: int | None) -> float | None:
    total = int(for_count or 0) + int(against_count or 0)
    if total <= 0:
        return None
    return round(100.0 * int(for_count or 0) / total, 1)


def _estimated_goalie_gsaa(st: PlayerGoalieStat, league_sv_pct: float | None) -> float | None:
    if st.gsaa is not None:
        return st.gsaa
    if league_sv_pct is None or not st.sa:
        return None
    actual_ga = int(st.ga or 0)
    expected_ga = (1.0 - league_sv_pct) * int(st.sa or 0)
    return round(expected_ga - actual_ga, 2)


def _league_goalie_sv_pct(rows: list[PlayerGoalieStat]) -> float | None:
    shots_against = sum(int(r.sa or 0) for r in rows)
    goals_against = sum(int(r.ga or 0) for r in rows)
    if shots_against <= 0:
        return None
    return max(0.0, min(1.0, (shots_against - goals_against) / shots_against))


def _recent_game_ids_for_player(
    session: Session,
    player_id: int,
    *,
    window: int = 10,
) -> list[int]:
    rows = session.execute(
        select(GameSkaterStat.game_id, Game.game_date)
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(GameSkaterStat.player_id == player_id, Game.status == "final")
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(window * 3)
    ).all()
    seen: set[int] = set()
    out: list[int] = []
    for gid, _ in rows:
        if gid in seen:
            continue
        seen.add(gid)
        out.append(int(gid))
        if len(out) >= window:
            break
    return out


def _fo_pct(wins: int | None, losses: int | None = None, *, total: int | None = None) -> float | None:
    if total is not None and total > 0:
        return round(100.0 * int(wins or 0) / total, 1)
    w = int(wins or 0)
    l = int(losses or 0)
    denom = w + l
    if denom <= 0:
        return None
    return round(100.0 * w / denom, 1)


def _aggregate_game_skater_lines(session: Session, player_id: int, game_ids: list[int]) -> dict[str, Any]:
    if not game_ids:
        return {}
    lines = session.scalars(
        select(GameSkaterStat)
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(
            GameSkaterStat.player_id == player_id,
            GameSkaterStat.game_id.in_(game_ids),
        )
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
    ).all()
    if not lines:
        return {}
    oz = sum(int(l.oz_starts or 0) for l in lines)
    nz = sum(int(l.nz_starts or 0) for l in lines)
    dz = sum(int(l.dz_starts or 0) for l in lines)
    sq_counts = {k: sum(int(getattr(l, k) or 0) for l in lines) for k in SQ_KEYS}
    goals = sum(int(l.goals or 0) for l in lines)
    assists = sum(int(l.assists or 0) for l in lines)
    shots = sum(int(l.shots or 0) for l in lines)
    toi_seconds = sum(int(l.toi_seconds or 0) for l in lines)
    latest_team_id = next((int(l.team_id) for l in lines if l.team_id), None)
    sq = sq_profile_from_counts(sq_counts)
    points = goals + assists
    return {
        "gp": len(game_ids),
        "team_id": latest_team_id,
        "goals": goals,
        "assists": assists,
        "points": points,
        "shots": shots,
        "sf_per_60": round(shots / (toi_seconds / 3600.0), 2) if toi_seconds > 0 else None,
        "zone_starts": zone_start_pcts(oz, nz, dz),
        "sq": sq,
        "high_danger_share": sq.get("high_danger_share"),
    }


def _skater_game_event_profile(game_lines: list[GameSkaterStat]) -> dict[str, Any]:
    if not game_lines:
        return {}
    sog = sum(int(l.shots or 0) for l in game_lines)
    missed = sum(int(l.missed_shots or 0) for l in game_lines)
    blocked = sum(int(l.blocked_shots or 0) for l in game_lines)
    hits = sum(int(l.hits or 0) for l in game_lines)
    takeaways = sum(int(l.takeaways or 0) for l in game_lines)
    giveaways = sum(int(l.giveaways or 0) for l in game_lines)
    fow = sum(int(l.faceoffs_won or 0) for l in game_lines)
    fol = sum(int(l.faceoffs_lost or 0) for l in game_lines)
    return {
        "sog": sog,
        "missed_shots": missed,
        "blocked_shots": blocked,
        "hits": hits,
        "takeaways": takeaways,
        "giveaways": giveaways,
        "fo_pct": _fo_pct(fow, fol),
    }


def _goalie_saves(st: PlayerGoalieStat | GameGoalieStat) -> int | None:
    sa = int(getattr(st, "sa", None) or getattr(st, "shots_against", None) or 0)
    ga = int(getattr(st, "ga", None) or getattr(st, "goals_allowed", None) or 0)
    explicit = getattr(st, "saves", None)
    if explicit is not None and int(explicit) > 0:
        return int(explicit)
    if sa > 0:
        return max(0, sa - ga)
    return None


def _goalie_record_label(st: PlayerGoalieStat) -> str | None:
    w = int(st.wins or 0)
    l = int(st.losses or 0)
    otl = int(st.otl or 0)
    if w + l + otl <= 0:
        return None
    return f"{w}-{l}-{otl}"


def _goalie_season_process_snapshot(st: PlayerGoalieStat, league_sv_pct: float | None) -> dict[str, Any]:
    gsaa = _estimated_goalie_gsaa(st, league_sv_pct)
    return {
        "gp": st.gp,
        "games_started": st.games_started,
        "minutes_played": st.minutes_played,
        "record": _goalie_record_label(st),
        "wins": st.wins,
        "losses": st.losses,
        "otl": st.otl,
        "sa": st.sa,
        "ga": st.ga,
        "saves": _goalie_saves(st),
        "so": st.so,
        "sv_pct": st.sv_pct,
        "gaa": st.gaa,
        "game_rating": st.game_rating,
        "gsaa": gsaa,
        "gsaa_estimated": st.gsaa is None and gsaa is not None,
    }


def _goalie_game_log_profile(game_lines: list[GameGoalieStat]) -> dict[str, Any]:
    if not game_lines:
        return {}
    ratings = [float(l.game_rating) for l in game_lines if l.game_rating is not None]
    toi_seconds = sum(int(l.toi_seconds or 0) for l in game_lines)
    shutouts = sum(1 for l in game_lines if int(l.goals_allowed or 0) == 0 and int(l.shots_against or 0) > 0)
    return {
        "gp": len(game_lines),
        "avg_game_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "toi_seconds": toi_seconds if toi_seconds > 0 else None,
        "shutouts": shutouts,
    }


def _skater_season_process_snapshot(st: PlayerSkaterStat) -> dict[str, Any]:
    return {
        "gp": st.gp,
        "cf_pct": st.cf_pct if st.cf_pct is not None else _pct_from_pair(st.cf, st.ca),
        "ff_pct": st.ff_pct if st.ff_pct is not None else _pct_from_pair(st.ff, st.fa),
        "cf": st.cf,
        "ca": st.ca,
        "ff": st.ff,
        "fa": st.fa,
        "cf_pct_rel": st.cf_pct_rel,
        "ff_pct_rel": st.ff_pct_rel,
        "sf_per_60": st.sf_per_60,
        "sa_per_60": st.sa_per_60,
        "gf_per_60": st.gf_per_60,
        "ga_per_60": st.ga_per_60,
        "pts_per_60": _player_pts_per_60(st),
        "pp_pts_per_60": _player_pp_pts_per_60(st),
        "sh_pts_per_60": _player_sh_pts_per_60(st),
        "pdo": st.pdo,
        "pdo_band": pdo_band(st.pdo),
        "sog": st.shots,
        "blocked_shots": st.blocked_shots,
        "hits": st.hits,
        "takeaways": st.takeaways,
        "giveaways": st.giveaways,
        "fo_pct": _fo_pct(st.faceoff_wins, total=st.faceoffs),
    }


def build_skater_leaderboard_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> list[dict[str, Any]]:
    min_gp = _adaptive_min_gp(session, PlayerSkaterStat, season_id, segment, MIN_SKATER_GP)
    rows = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
        .where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
            PlayerSkaterStat.gp >= min_gp,
        )
    ).all()
    require_toi = _has_full_toi_sample(session, season_id, segment)
    out: list[dict[str, Any]] = []
    for st in rows:
        if require_toi and (st.toi_seconds or 0) < MIN_SKATER_TOI_SECONDS:
            continue
        pl = st.player
        if pl is None:
            continue
        out.append(
            {
                "player_id": int(pl.id),
                "player_name": pl.full_name,
                "team": st.team,
                "gp": st.gp,
                "cf_pct": st.cf_pct if st.cf_pct is not None else _pct_from_pair(st.cf, st.ca),
                "ff_pct": st.ff_pct if st.ff_pct is not None else _pct_from_pair(st.ff, st.fa),
                "sf_per_60": st.sf_per_60,
                "pdo": st.pdo,
                "pts_per_60": _player_pts_per_60(st),
                "pp_pts_per_60": _player_pp_pts_per_60(st),
                "sh_pts_per_60": _player_sh_pts_per_60(st),
                "pdo_band": pdo_band(st.pdo),
            }
        )
    if any(r.get("sf_per_60") is None for r in out):
        for row in out:
            if row.get("sf_per_60") is not None:
                continue
            recent = _aggregate_game_skater_lines(
                session,
                int(row["player_id"]),
                _recent_game_ids_for_player(session, int(row["player_id"]), window=10),
            )
            row["sf_per_60"] = recent.get("sf_per_60")
            row["sf_per_60_source"] = "last_10" if recent.get("sf_per_60") is not None else None
    return out


def build_goalie_leaderboard_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> list[dict[str, Any]]:
    min_gp = _adaptive_min_gp(session, PlayerGoalieStat, season_id, segment, MIN_GOALIE_GP)
    rows = session.scalars(
        select(PlayerGoalieStat)
        .options(joinedload(PlayerGoalieStat.player), joinedload(PlayerGoalieStat.team))
        .where(
            PlayerGoalieStat.season_id == season_id,
            PlayerGoalieStat.stat_segment == segment,
            PlayerGoalieStat.gp >= min_gp,
        )
    ).all()
    league_sv_pct = _league_goalie_sv_pct(rows)
    out: list[dict[str, Any]] = []
    for st in rows:
        pl = st.player
        if pl is None:
            continue
        season = _goalie_season_process_snapshot(st, league_sv_pct)
        out.append(
            {
                "player_id": int(pl.id),
                "player_name": pl.full_name,
                "team": st.team,
                **season,
            }
        )
    return out


def _team_sq_totals_from_games(session: Session, season_id: int, team_id: int) -> dict[str, int]:
    games = session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status == "final",
            (Game.home_team_id == team_id) | (Game.away_team_id == team_id),
        )
    ).all()
    counts = {k: 0 for k in SQ_KEYS}
    for g in games:
        home = int(g.home_team_id) == int(team_id)
        for i in range(5):
            key = SQ_KEYS[i]
            val = getattr(g, f"sq{i}_{'home' if home else 'away'}", None)
            counts[key] += int(val or 0)
    return counts


def build_team_process_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> list[dict[str, Any]]:
    standings = {
        int(s.team_id): s
        for s in session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    }
    aggs = session.scalars(
        select(TeamSeasonAggregate)
        .options(joinedload(TeamSeasonAggregate.team))
        .where(TeamSeasonAggregate.season_id == season_id, TeamSeasonAggregate.stat_segment == segment)
    ).all()
    out: list[dict[str, Any]] = []
    for agg in aggs:
        tm = agg.team
        if tm is None:
            continue
        st = standings.get(int(tm.id))
        gf = int(st.gf or 0) if st else None
        ga = int(st.ga or 0) if st else None
        sf = int(agg.shots_for or 0)
        sa = int(agg.shots_against or 0)
        shot_diff = sf - sa if sf or sa else None
        sq = sq_profile_from_counts(_team_sq_totals_from_games(session, season_id, int(tm.id)))
        out.append(
            {
                "team_id": int(tm.id),
                "team": tm,
                "gp": int(st.gp or 0) if st else None,
                "shot_diff": shot_diff,
                "sa_diff": (ga - sa) if ga is not None and sa else None,
                "pp_pct": _pp_pct(agg.pp_goals, agg.pp_chances),
                "pk_pct": _pk_pct(agg.pk_goals_against, agg.sh_chances),
                "sq_high_danger": sq.get("high_danger_share"),
                "gf": gf,
                "ga": ga,
                "shots_for": sf,
                "shots_against": sa,
            }
        )
    return out


TEAM_CHART_METRICS: list[dict[str, Any]] = [
    {"key": "gf", "label": "Goals For", "per_game": True, "decimals": 2, "better": "high"},
    {"key": "ga", "label": "Goals Against", "per_game": True, "decimals": 2, "better": "low"},
    {"key": "goal_diff", "label": "Goal Differential", "per_game": True, "decimals": 2, "better": "high"},
    {"key": "shots_for", "label": "Shots For", "per_game": True, "decimals": 2, "better": "high"},
    {"key": "shots_against", "label": "Shots Against", "per_game": True, "decimals": 2, "better": "low"},
    {"key": "shot_diff", "label": "Shot Differential", "per_game": True, "decimals": 2, "better": "high"},
    {"key": "pp_pct", "label": "Power Play %", "per_game": False, "decimals": 1, "better": "high"},
    {"key": "pk_pct", "label": "Penalty Kill %", "per_game": False, "decimals": 1, "better": "high"},
    {"key": "sq_high_danger", "label": "High-Danger SQ %", "per_game": False, "decimals": 1, "better": "high"},
    {"key": "pts", "label": "Standings Points", "per_game": True, "decimals": 2, "better": "high"},
    {"key": "point_pct", "label": "Points %", "per_game": False, "decimals": 1, "better": "high"},
    {"key": "points_above_ppg", "label": "Points Above PPG", "per_game": False, "decimals": 1, "better": "high"},
]

TEAM_CHART_SEGMENTS: list[dict[str, str]] = [
    {"key": "rs", "label": "Regular Season"},
    {"key": "ps", "label": "Playoffs"},
    {"key": "po", "label": "Preseason"},
]


def _team_chart_metric_values(row: dict[str, Any]) -> dict[str, float | int | None]:
    gp = int(row.get("gp") or 0)
    gf = row.get("gf")
    ga = row.get("ga")
    goal_diff = (int(gf) - int(ga)) if gf is not None and ga is not None else None
    pts = row.get("pts")
    point_pct = round(100.0 * int(pts) / (gp * 2), 1) if pts is not None and gp > 0 else None
    points_above_ppg = (int(pts) - gp) if pts is not None and gp > 0 else None
    return {
        "gp": gp if gp > 0 else None,
        "gf": gf,
        "ga": ga,
        "goal_diff": goal_diff,
        "shots_for": row.get("shots_for"),
        "shots_against": row.get("shots_against"),
        "shot_diff": row.get("shot_diff"),
        "pp_pct": row.get("pp_pct"),
        "pk_pct": row.get("pk_pct"),
        "sq_high_danger": row.get("sq_high_danger"),
        "pts": pts,
        "point_pct": point_pct,
        "points_above_ppg": points_above_ppg,
    }


def _seasons_with_team_chart_data(session: Session) -> list[Season]:
    agg_ids = {
        int(x)
        for x in session.scalars(select(TeamSeasonAggregate.season_id).distinct()).all()
        if x is not None
    }
    stand_ids = {
        int(x) for x in session.scalars(select(TeamStanding.season_id).distinct()).all() if x is not None
    }
    season_ids = agg_ids | stand_ids
    if not season_ids:
        return []
    return list(
        session.scalars(
            select(Season)
            .where(Season.id.in_(season_ids))
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        ).all()
    )


def build_team_analytics_chart_archive(
    session: Session,
    *,
    default_season_id: int | None = None,
    default_segment: str = "rs",
) -> dict[str, Any]:
    from app.services.season_team_logo_bundle import get_season_team_logo_bundle
    from app.services.seasons import season_display_label

    logo_bundle = get_season_team_logo_bundle()
    seasons = _seasons_with_team_chart_data(session)
    standings_by_season: dict[int, dict[int, TeamStanding]] = {}
    for st in session.scalars(select(TeamStanding)).all():
        standings_by_season.setdefault(int(st.season_id), {})[int(st.team_id)] = st

    datasets: dict[str, dict[str, Any]] = {}
    season_options: list[dict[str, Any]] = []
    for season in seasons:
        season_key = int(season.id)
        season_has_data = False
        for segment in ("rs", "ps", "po"):
            team_rows = build_team_process_rows(session, season_key, segment=segment)
            if not team_rows:
                continue
            standings = standings_by_season.get(season_key, {})
            payload_rows: list[dict[str, Any]] = []
            for row in team_rows:
                tm = row.get("team")
                if tm is None:
                    continue
                st = standings.get(int(row["team_id"]))
                enriched = {
                    **row,
                    "pts": int(st.pts) if st and st.pts is not None else None,
                    "gp": int(st.gp or 0) if st else row.get("gp"),
                }
                metrics = _team_chart_metric_values(enriched)
                if not any(v is not None for k, v in metrics.items() if k != "gp"):
                    continue
                sy = season.start_year
                payload_rows.append(
                    {
                        "team_id": int(row["team_id"]),
                        "name": tm.full_display_name(),
                        "abbr": (tm.abbreviation or tm.name or "").strip(),
                        "slug": tm.slug,
                        "logo_url": logo_bundle.team_logo_url_for_season_context(tm, sy),
                        "primary_color": tm.primary_color,
                        "metrics": metrics,
                    }
                )
            if payload_rows:
                datasets[f"{season_key}|{segment}"] = {"teams": payload_rows}
                season_has_data = True
        if season_has_data:
            season_options.append(
                {
                    "id": season_key,
                    "label": season_display_label(season),
                    "start_year": season.start_year,
                }
            )

    if default_season_id is None and season_options:
        default_season_id = int(season_options[0]["id"])
    if default_segment not in {s["key"] for s in TEAM_CHART_SEGMENTS}:
        default_segment = "rs"

    return {
        "metrics": TEAM_CHART_METRICS,
        "segments": TEAM_CHART_SEGMENTS,
        "seasons": season_options,
        "default_season_id": default_season_id,
        "default_segment": default_segment,
        "default_x": "gf",
        "default_y": "ga",
        "default_norm": "per_game",
        "datasets": datasets,
    }


TEAM_PLAYER_SKATER_METRICS: list[dict[str, Any]] = [
    {"key": "goals", "label": "Goals", "per_game": True, "per_60": True, "decimals": 0, "better": "high"},
    {"key": "assists", "label": "Assists", "per_game": True, "per_60": True, "decimals": 0, "better": "high"},
    {"key": "points", "label": "Points", "per_game": True, "per_60": True, "decimals": 0, "better": "high"},
    {"key": "shots", "label": "Shots", "per_game": True, "per_60": True, "decimals": 0, "better": "high"},
    {"key": "cf_pct", "label": "CF%", "per_game": False, "per_60": False, "decimals": 1, "better": "high"},
    {"key": "ff_pct", "label": "FF%", "per_game": False, "per_60": False, "decimals": 1, "better": "high"},
    {"key": "sf_per_60", "label": "SF/60", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
    {"key": "pts_per_60", "label": "PTS/60", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
    {"key": "pp_pts_per_60", "label": "PP PTS/60", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
    {"key": "pdo", "label": "PDO", "per_game": False, "per_60": False, "decimals": 1, "better": "neutral"},
    {"key": "high_danger_share", "label": "High-Danger SQ %", "per_game": False, "per_60": False, "decimals": 1, "better": "high"},
]

TEAM_PLAYER_GOALIE_METRICS: list[dict[str, Any]] = [
    {"key": "sv_pct", "label": "SV%", "per_game": False, "per_60": False, "decimals": 3, "better": "high"},
    {"key": "gsaa", "label": "GSAA", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
    {"key": "gaa", "label": "GAA", "per_game": False, "per_60": False, "decimals": 2, "better": "low"},
    {"key": "sa", "label": "Shots Against", "per_game": True, "per_60": True, "decimals": 0, "better": "neutral"},
    {"key": "ga", "label": "Goals Allowed", "per_game": True, "per_60": True, "decimals": 0, "better": "low"},
    {"key": "saves", "label": "Saves", "per_game": True, "per_60": True, "decimals": 0, "better": "high"},
    {"key": "so", "label": "Shutouts", "per_game": False, "per_60": False, "decimals": 0, "better": "high"},
    {"key": "game_rating", "label": "Game Rating", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
]


def _seasons_with_team_player_chart_data(session: Session, team_id: int) -> list[Season]:
    skater_ids = {
        int(x)
        for x in session.scalars(
            select(PlayerSkaterStat.season_id)
            .where(PlayerSkaterStat.team_id == team_id)
            .distinct()
        ).all()
        if x is not None
    }
    goalie_ids = {
        int(x)
        for x in session.scalars(
            select(PlayerGoalieStat.season_id)
            .where(PlayerGoalieStat.team_id == team_id)
            .distinct()
        ).all()
        if x is not None
    }
    season_ids = skater_ids | goalie_ids
    if not season_ids:
        return []
    return list(
        session.scalars(
            select(Season)
            .where(Season.id.in_(season_ids))
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        ).all()
    )


def _skater_player_chart_metrics(
    session: Session,
    st: PlayerSkaterStat,
    *,
    season_id: int,
) -> dict[str, float | int | None]:
    snap = _skater_season_process_snapshot(st)
    game_lines = session.scalars(
        select(GameSkaterStat)
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(
            GameSkaterStat.player_id == st.player_id,
            Game.season_id == season_id,
            Game.status == "final",
        )
    ).all()
    sq_counts = {k: sum(int(getattr(l, k) or 0) for l in game_lines) for k in SQ_KEYS}
    sq = sq_profile_from_counts(sq_counts)
    toi_hours = (st.toi_seconds or 0) / 3600.0 if st.toi_seconds else 0.0

    def per_60(count: int | None) -> float | None:
        if count is None or toi_hours <= 0:
            return None
        return round(float(count) / toi_hours, 2)

    return {
        "gp": st.gp,
        "toi_seconds": st.toi_seconds,
        "goals": st.goals,
        "assists": st.assists,
        "points": st.points,
        "goals_per_60": per_60(st.goals),
        "assists_per_60": per_60(st.assists),
        "shots": st.shots,
        "shots_per_60": per_60(st.shots),
        "cf_pct": snap.get("cf_pct"),
        "ff_pct": snap.get("ff_pct"),
        "sf_per_60": snap.get("sf_per_60"),
        "pts_per_60": snap.get("pts_per_60"),
        "pp_pts_per_60": snap.get("pp_pts_per_60"),
        "sh_pts_per_60": snap.get("sh_pts_per_60"),
        "pdo": snap.get("pdo"),
        "high_danger_share": sq.get("high_danger_share"),
    }


def _goalie_player_chart_metrics(
    st: PlayerGoalieStat,
    league_sv_pct: float | None,
) -> dict[str, float | int | None]:
    snap = _goalie_season_process_snapshot(st, league_sv_pct)
    minutes = int(st.minutes_played or 0)
    hours = minutes / 60.0 if minutes > 0 else 0.0

    def per_60(count: int | None) -> float | None:
        if count is None or hours <= 0:
            return None
        return round(float(count) / hours, 2)

    return {
        "gp": snap.get("gp"),
        "minutes_played": minutes,
        "sv_pct": snap.get("sv_pct"),
        "gsaa": snap.get("gsaa"),
        "gaa": snap.get("gaa"),
        "sa": snap.get("sa"),
        "ga": snap.get("ga"),
        "saves": snap.get("saves"),
        "so": snap.get("so"),
        "game_rating": snap.get("game_rating"),
        "sa_per_60": per_60(snap.get("sa")),
        "ga_per_60": per_60(snap.get("ga")),
        "saves_per_60": per_60(snap.get("saves")),
    }


def build_team_player_analytics_archive(
    session: Session,
    team: Team,
    *,
    default_season_id: int | None = None,
    default_segment: str = "rs",
    static_folder: str | Path | None = None,
) -> dict[str, Any]:
    from app.services.player_headshot import resolve_player_headshot_static_filename
    from app.services.seasons import season_display_label

    team_id = int(team.id)
    seasons = _seasons_with_team_player_chart_data(session, team_id)
    datasets: dict[str, dict[str, Any]] = {}
    season_options: list[dict[str, Any]] = []
    static_root = Path(static_folder) if static_folder else None

    for season in seasons:
        season_key = int(season.id)
        season_has_data = False
        for segment in ("rs", "ps", "po"):
            league_goalies = list(
                session.scalars(
                    select(PlayerGoalieStat).where(
                        PlayerGoalieStat.season_id == season_key,
                        PlayerGoalieStat.stat_segment == segment,
                    )
                ).all()
            )
            league_sv = _league_goalie_sv_pct(league_goalies)

            skaters = session.scalars(
                select(PlayerSkaterStat)
                .options(joinedload(PlayerSkaterStat.player))
                .where(
                    PlayerSkaterStat.season_id == season_key,
                    PlayerSkaterStat.team_id == team_id,
                    PlayerSkaterStat.stat_segment == segment,
                    PlayerSkaterStat.gp > 0,
                )
            ).all()
            skater_rows: list[dict[str, Any]] = []
            for st in skaters:
                pl = st.player
                if pl is None:
                    continue
                metrics = _skater_player_chart_metrics(session, st, season_id=season_key)
                if not any(v is not None for k, v in metrics.items() if k not in ("gp", "toi_seconds")):
                    continue
                headshot_rel = (
                    resolve_player_headshot_static_filename(static_root, pl)
                    if static_root is not None
                    else None
                )
                skater_rows.append(
                    {
                        "player_id": int(pl.id),
                        "name": pl.full_name,
                        "position": (pl.position or "").strip(),
                        "headshot_rel": headshot_rel,
                        "metrics": metrics,
                    }
                )
            if skater_rows:
                datasets[f"{season_key}|{segment}|skater"] = {"players": skater_rows}
                season_has_data = True

            goalies = session.scalars(
                select(PlayerGoalieStat)
                .options(joinedload(PlayerGoalieStat.player))
                .where(
                    PlayerGoalieStat.season_id == season_key,
                    PlayerGoalieStat.team_id == team_id,
                    PlayerGoalieStat.stat_segment == segment,
                    PlayerGoalieStat.gp > 0,
                )
            ).all()
            goalie_rows: list[dict[str, Any]] = []
            for st in goalies:
                pl = st.player
                if pl is None:
                    continue
                metrics = _goalie_player_chart_metrics(st, league_sv)
                if not any(v is not None for k, v in metrics.items() if k not in ("gp", "minutes_played")):
                    continue
                headshot_rel = (
                    resolve_player_headshot_static_filename(static_root, pl)
                    if static_root is not None
                    else None
                )
                goalie_rows.append(
                    {
                        "player_id": int(pl.id),
                        "name": pl.full_name,
                        "position": (pl.position or "G").strip(),
                        "headshot_rel": headshot_rel,
                        "metrics": metrics,
                    }
                )
            if goalie_rows:
                datasets[f"{season_key}|{segment}|goalie"] = {"players": goalie_rows}
                season_has_data = True

        if season_has_data:
            season_options.append(
                {
                    "id": season_key,
                    "label": season_display_label(season),
                    "start_year": season.start_year,
                }
            )

    if default_season_id is None and season_options:
        default_season_id = int(season_options[0]["id"])
    if default_segment not in {s["key"] for s in TEAM_CHART_SEGMENTS}:
        default_segment = "rs"

    return {
        "team_id": team_id,
        "team_name": team.full_display_name(),
        "skater_metrics": TEAM_PLAYER_SKATER_METRICS,
        "goalie_metrics": TEAM_PLAYER_GOALIE_METRICS,
        "segments": TEAM_CHART_SEGMENTS,
        "seasons": season_options,
        "default_season_id": default_season_id,
        "default_segment": default_segment,
        "default_kind": "skater",
        "default_x_skater": "points",
        "default_y_skater": "cf_pct",
        "default_x_goalie": "sv_pct",
        "default_y_goalie": "gsaa",
        "default_norm": "per_game",
        "datasets": datasets,
    }


TEAM_PLAYER_TREND_SKATER_METRICS: list[dict[str, Any]] = [
    {"key": "goals", "label": "Goals", "mode": "sum", "decimals": 0},
    {"key": "assists", "label": "Assists", "mode": "sum", "decimals": 0},
    {"key": "points", "label": "Points", "mode": "sum", "decimals": 0},
    {"key": "shots", "label": "Shots", "mode": "sum", "decimals": 0},
    {"key": "hits", "label": "Hits", "mode": "sum", "decimals": 0},
    {"key": "blocked_shots", "label": "Blocked Shots", "mode": "sum", "decimals": 0},
    {"key": "missed_shots", "label": "Missed Shots", "mode": "sum", "decimals": 0},
    {"key": "takeaways", "label": "Takeaways", "mode": "sum", "decimals": 0},
    {"key": "giveaways", "label": "Giveaways", "mode": "sum", "decimals": 0},
    {"key": "pim", "label": "PIM", "mode": "sum", "decimals": 0},
    {"key": "high_danger_attempts", "label": "High-Danger Attempts", "mode": "sum", "decimals": 0},
    {
        "key": "high_danger_share",
        "label": "High-Danger SQ %",
        "mode": "ratio",
        "num": "high_danger_attempts",
        "den": "sq_total",
        "scale": 100,
        "decimals": 1,
    },
    {
        "key": "team_shot_share",
        "label": "On-Ice Shot Share %",
        "mode": "ratio",
        "num": "team_shots_off",
        "den": "team_shots_total",
        "scale": 100,
        "decimals": 1,
    },
]

TEAM_PLAYER_TREND_GOALIE_METRICS: list[dict[str, Any]] = [
    {"key": "saves", "label": "Saves", "mode": "sum", "decimals": 0},
    {"key": "sa", "label": "Shots Against", "mode": "sum", "decimals": 0},
    {"key": "ga", "label": "Goals Allowed", "mode": "sum", "decimals": 0},
    {
        "key": "sv_pct",
        "label": "SV%",
        "mode": "ratio",
        "num": "saves",
        "den": "sa",
        "scale": 1,
        "decimals": 3,
    },
    {"key": "gaa", "label": "GAA", "mode": "gaa", "decimals": 2},
    {"key": "game_rating", "label": "Game Rating", "mode": "avg", "decimals": 2},
]

TEAM_PLAYER_TREND_POSITION_FILTERS: list[dict[str, str]] = [
    {"key": "all", "label": "All"},
    {"key": "forwards", "label": "Forwards"},
    {"key": "defense", "label": "Defense"},
    {"key": "goalies", "label": "Goalies"},
]


def _team_player_trend_game_segment_filter(segment: str):
    """Map TEAM_CHART_SEGMENTS keys (rs/ps/po) to ``Game.game_type`` filters."""
    if segment == "ps":
        return or_(
            Game.game_type.ilike("%playoff%"),
            Game.game_type.ilike("%post%"),
            Game.game_type.ilike("%stanley%"),
        )
    if segment == "po":
        return or_(
            Game.game_type.ilike("%preseason%"),
            Game.game_type.ilike("%pre-season%"),
            Game.game_type.ilike("%exhibition%"),
        )
    return or_(
        Game.game_type.is_(None),
        Game.game_type.ilike("%regular%"),
        and_(
            ~Game.game_type.ilike("%playoff%"),
            ~Game.game_type.ilike("%post%"),
            ~Game.game_type.ilike("%stanley%"),
            ~Game.game_type.ilike("%preseason%"),
            ~Game.game_type.ilike("%pre-season%"),
            ~Game.game_type.ilike("%exhibition%"),
        ),
    )


def _is_forward_position(position: str | None) -> bool:
    pos = (position or "").strip().upper()
    if not pos:
        return False
    if pos in ("C", "LW", "RW", "W", "F", "LF", "RF", "LC", "RC"):
        return True
    return pos.startswith("F")


def _is_defense_position(position: str | None) -> bool:
    pos = (position or "").strip().upper()
    if not pos:
        return False
    if pos in ("D", "LD", "RD", "DF"):
        return True
    return pos.startswith("D")


def _is_goalie_position(position: str | None) -> bool:
    return (position or "").strip().upper() in ("G", "GK")


def _skater_trend_game_counts(line: GameSkaterStat) -> dict[str, int | float | None]:
    goals = int(line.goals or 0)
    assists = int(line.assists or 0)
    sq3 = int(line.sq3 or 0)
    sq4 = int(line.sq4 or 0)
    sq_total = sum(int(getattr(line, k) or 0) for k in SQ_KEYS)
    team_shots_off = line.team_shots_off
    team_shots_against = line.team_shots_against_off
    team_shots_total: int | None = None
    if team_shots_off is not None and team_shots_against is not None:
        team_shots_total = int(team_shots_off) + int(team_shots_against)
    return {
        "goals": goals,
        "assists": assists,
        "points": goals + assists,
        "shots": int(line.shots or 0),
        "hits": int(line.hits or 0) if line.hits is not None else 0,
        "blocked_shots": int(line.blocked_shots or 0) if line.blocked_shots is not None else 0,
        "missed_shots": int(line.missed_shots or 0) if line.missed_shots is not None else 0,
        "takeaways": int(line.takeaways or 0) if line.takeaways is not None else 0,
        "giveaways": int(line.giveaways or 0) if line.giveaways is not None else 0,
        "pim": int(line.pim or 0),
        "sq3": sq3,
        "sq4": sq4,
        "high_danger_attempts": sq3 + sq4,
        "sq_total": sq_total,
        "team_shots_off": int(team_shots_off) if team_shots_off is not None else None,
        "team_shots_against_off": int(team_shots_against) if team_shots_against is not None else None,
        "team_shots_total": team_shots_total,
    }


def _goalie_trend_game_counts(line: GameGoalieStat) -> dict[str, int | float | None]:
    sa = int(line.shots_against or 0)
    saves = int(line.saves or 0)
    ga = int(line.goals_allowed or 0)
    toi_seconds = int(line.toi_seconds or 0) if line.toi_seconds else 0
    rating = float(line.game_rating) if line.game_rating is not None else None
    return {
        "saves": saves,
        "sa": sa,
        "ga": ga,
        "toi_seconds": toi_seconds,
        "game_rating": rating,
    }


def _seasons_with_team_player_trend_data(session: Session, team_id: int) -> list[Season]:
    skater_ids = {
        int(x)
        for x in session.scalars(
            select(Game.season_id)
            .join(GameSkaterStat, GameSkaterStat.game_id == Game.id)
            .where(GameSkaterStat.team_id == team_id, Game.status == "final")
            .distinct()
        ).all()
        if x is not None
    }
    goalie_ids = {
        int(x)
        for x in session.scalars(
            select(Game.season_id)
            .join(GameGoalieStat, GameGoalieStat.game_id == Game.id)
            .where(GameGoalieStat.team_id == team_id, Game.status == "final")
            .distinct()
        ).all()
        if x is not None
    }
    season_ids = skater_ids | goalie_ids
    if not season_ids:
        return []
    return list(
        session.scalars(
            select(Season)
            .where(Season.id.in_(season_ids))
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        ).all()
    )


def _team_player_trend_game_meta(
    games: list[Game],
    line_game_ids: set[int],
) -> dict[int, dict[str, Any]]:
    game_meta: dict[int, dict[str, Any]] = {}
    idx = 0
    for game in games:
        game_id = int(game.id)
        if game_id not in line_game_ids:
            continue
        idx += 1
        game_date = game.game_date.isoformat() if game.game_date else None
        game_meta[game_id] = {
            "date": game_date,
            "game_number": idx,
        }
    return game_meta


def build_team_player_trends_archive(
    session: Session,
    team: Team,
    *,
    default_season_id: int | None = None,
    default_segment: str = "rs",
    static_folder: str | Path | None = None,
) -> dict[str, Any]:
    from app.services.player_headshot import resolve_player_headshot_static_filename
    from app.services.seasons import season_display_label

    team_id = int(team.id)
    seasons = _seasons_with_team_player_trend_data(session, team_id)
    datasets: dict[str, dict[str, Any]] = {}
    season_options: list[dict[str, Any]] = []
    static_root = Path(static_folder) if static_folder else None

    for season in seasons:
        season_key = int(season.id)
        season_has_data = False
        for segment in ("rs", "ps", "po"):
            games = session.scalars(
                select(Game)
                .where(
                    Game.season_id == season_key,
                    Game.status == "final",
                    or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
                    _team_player_trend_game_segment_filter(segment),
                )
                .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            ).all()
            if not games:
                continue

            skater_lines = session.scalars(
                select(GameSkaterStat)
                .options(joinedload(GameSkaterStat.player))
                .join(Game, Game.id == GameSkaterStat.game_id)
                .where(
                    GameSkaterStat.team_id == team_id,
                    Game.season_id == season_key,
                    Game.status == "final",
                    _team_player_trend_game_segment_filter(segment),
                )
                .order_by(Game.game_date.asc().nulls_last(), Game.id.asc(), GameSkaterStat.id.asc())
            ).all()
            skater_game_meta = _team_player_trend_game_meta(
                games,
                {int(line.game_id) for line in skater_lines},
            )
            skater_series_by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
            skater_meta: dict[int, dict[str, Any]] = {}
            for line in skater_lines:
                meta = skater_game_meta.get(int(line.game_id))
                if meta is None:
                    continue
                pl = line.player
                if pl is None:
                    continue
                pid = int(pl.id)
                if pid not in skater_meta:
                    headshot_rel = (
                        resolve_player_headshot_static_filename(static_root, pl)
                        if static_root is not None
                        else None
                    )
                    skater_meta[pid] = {
                        "player_id": pid,
                        "name": pl.full_name,
                        "position": (pl.position or "").strip(),
                        "headshot_rel": headshot_rel,
                    }
                skater_series_by_player[pid].append(
                    {
                        "date": meta["date"],
                        "game_number": meta["game_number"],
                        "counts": _skater_trend_game_counts(line),
                    }
                )

            skater_players: list[dict[str, Any]] = []
            for pid, points in skater_series_by_player.items():
                if not points:
                    continue
                skater_players.append({**skater_meta[pid], "series": points})
            if skater_players:
                skater_players.sort(key=lambda row: (-len(row["series"]), row["name"] or ""))
                datasets[f"{season_key}|{segment}|skater"] = {
                    "game_count": len(skater_game_meta),
                    "players": skater_players,
                }
                season_has_data = True

            goalie_lines = session.scalars(
                select(GameGoalieStat)
                .options(joinedload(GameGoalieStat.player))
                .join(Game, Game.id == GameGoalieStat.game_id)
                .where(
                    GameGoalieStat.team_id == team_id,
                    Game.season_id == season_key,
                    Game.status == "final",
                    _team_player_trend_game_segment_filter(segment),
                )
                .order_by(Game.game_date.asc().nulls_last(), Game.id.asc(), GameGoalieStat.id.asc())
            ).all()
            goalie_game_meta = _team_player_trend_game_meta(
                games,
                {int(line.game_id) for line in goalie_lines},
            )
            goalie_series_by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
            goalie_meta: dict[int, dict[str, Any]] = {}
            for line in goalie_lines:
                meta = goalie_game_meta.get(int(line.game_id))
                if meta is None:
                    continue
                pl = line.player
                if pl is None:
                    continue
                pid = int(pl.id)
                if pid not in goalie_meta:
                    headshot_rel = (
                        resolve_player_headshot_static_filename(static_root, pl)
                        if static_root is not None
                        else None
                    )
                    goalie_meta[pid] = {
                        "player_id": pid,
                        "name": pl.full_name,
                        "position": (pl.position or "G").strip(),
                        "headshot_rel": headshot_rel,
                    }
                goalie_series_by_player[pid].append(
                    {
                        "date": meta["date"],
                        "game_number": meta["game_number"],
                        "counts": _goalie_trend_game_counts(line),
                    }
                )

            goalie_players: list[dict[str, Any]] = []
            for pid, points in goalie_series_by_player.items():
                if not points:
                    continue
                goalie_players.append({**goalie_meta[pid], "series": points})
            if goalie_players:
                goalie_players.sort(key=lambda row: (-len(row["series"]), row["name"] or ""))
                datasets[f"{season_key}|{segment}|goalie"] = {
                    "game_count": len(goalie_game_meta),
                    "players": goalie_players,
                }
                season_has_data = True

        if season_has_data:
            season_options.append(
                {
                    "id": season_key,
                    "label": season_display_label(season),
                    "start_year": season.start_year,
                }
            )

    if default_season_id is None and season_options:
        default_season_id = int(season_options[0]["id"])
    if default_segment not in {s["key"] for s in TEAM_CHART_SEGMENTS}:
        default_segment = "rs"

    return {
        "team_id": team_id,
        "team_name": team.full_display_name(),
        "skater_metrics": TEAM_PLAYER_TREND_SKATER_METRICS,
        "goalie_metrics": TEAM_PLAYER_TREND_GOALIE_METRICS,
        "position_filters": TEAM_PLAYER_TREND_POSITION_FILTERS,
        "segments": TEAM_CHART_SEGMENTS,
        "seasons": season_options,
        "default_season_id": default_season_id,
        "default_segment": default_segment,
        "default_kind": "skater",
        "default_metric_skater": "goals",
        "default_metric_goalie": "saves",
        "default_position_filter": "all",
        "datasets": datasets,
    }


TEAM_STATS_TREND_SITUATIONS: list[dict[str, str]] = [
    {"key": "all", "label": "All Situations"},
    {"key": "ev", "label": "5 on 5"},
    {"key": "pp", "label": "5 on 4"},
    {"key": "pk", "label": "4 on 5"},
    {"key": "other", "label": "Other"},
]

TEAM_STATS_TREND_BASIS: list[dict[str, str]] = [
    {"key": "totals", "label": "Totals"},
    {"key": "per_game", "label": "Per Game"},
]

TEAM_STATS_TREND_MODES: list[dict[str, str]] = [
    {"key": "cumulative", "label": "Season Cumulative To Date"},
    {"key": "game", "label": "Game Level"},
    {"key": "ma5", "label": "5 Game Moving Average"},
    {"key": "ma10", "label": "10 Game Moving Average"},
]

TEAM_STATS_TREND_METRICS: list[dict[str, Any]] = [
    {"key": "goal_diff", "label": "Goal Differential", "mode": "sum", "decimals": 0, "situations": ["all", "ev", "pp", "pk", "other"], "zero_line": True},
    {"key": "gf", "label": "Goals For", "mode": "sum", "decimals": 0, "situations": ["all", "ev", "pp", "pk", "other"]},
    {"key": "ga", "label": "Goals Against", "mode": "sum", "decimals": 0, "situations": ["all", "ev", "pp", "pk", "other"]},
    {
        "key": "goal_for_pct",
        "label": "Goal For %",
        "mode": "ratio",
        "num": "gf",
        "den": "goal_events",
        "scale": 100,
        "decimals": 1,
        "situations": ["all", "ev", "pp", "pk", "other"],
    },
    {"key": "sf", "label": "Shots For", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "sa", "label": "Shots Against", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "shot_diff", "label": "Shot Differential", "mode": "sum", "decimals": 0, "situations": ["all"], "zero_line": True},
    {
        "key": "shot_share",
        "label": "Shot Share %",
        "mode": "ratio",
        "num": "sf",
        "den": "shots_total",
        "scale": 100,
        "decimals": 1,
        "situations": ["all"],
    },
    {"key": "pp_goals", "label": "PP Goals", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "pp_opp", "label": "PP Opportunities", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {
        "key": "pp_pct",
        "label": "PP%",
        "mode": "ratio",
        "num": "pp_goals",
        "den": "pp_opp",
        "scale": 100,
        "decimals": 1,
        "situations": ["all"],
    },
    {"key": "pk_ga", "label": "PK Goals Against", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "pk_opp", "label": "PK Opportunities", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {
        "key": "pk_pct",
        "label": "PK%",
        "mode": "ratio",
        "num": "pk_stops",
        "den": "pk_opp",
        "scale": 100,
        "decimals": 1,
        "situations": ["all"],
    },
    {"key": "pim_for", "label": "PIM Drawn", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "pim_against", "label": "PIM Taken", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "pim_diff", "label": "PIM Differential", "mode": "sum", "decimals": 0, "situations": ["all"], "zero_line": True},
    {"key": "hits_for", "label": "Hits For", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "hits_against", "label": "Hits Against", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "hits_diff", "label": "Hits Differential", "mode": "sum", "decimals": 0, "situations": ["all"], "zero_line": True},
    {"key": "hd_for", "label": "High-Danger Attempts For", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "hd_against", "label": "High-Danger Attempts Against", "mode": "sum", "decimals": 0, "situations": ["all"]},
    {"key": "hd_diff", "label": "High-Danger Differential", "mode": "sum", "decimals": 0, "situations": ["all"], "zero_line": True},
    {
        "key": "hd_share",
        "label": "High-Danger SQ %",
        "mode": "ratio",
        "num": "hd_for",
        "den": "hd_total",
        "scale": 100,
        "decimals": 1,
        "situations": ["all"],
    },
    {"key": "standings_pts", "label": "Standings Points", "mode": "sum", "decimals": 0, "situations": ["all"]},
]


def _strength_situation_bucket(strength: str | None) -> str:
    s = (strength or "").strip().lower()
    if not s or s in ("ev", "even", "5v5", "5 on 5", "equal", "eq"):
        return "ev"
    if "pp" in s or "power" in s:
        return "pp"
    if "sh" in s or "short" in s or s in {"pk", "penalty kill"}:
        return "pk"
    return "other"


def _team_stats_optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _team_stats_optional_sum(*values: Any) -> int | None:
    if all(v is None for v in values):
        return None
    return sum(int(v or 0) for v in values)


def _team_stats_optional_diff(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return int(a) - int(b)


def _team_stats_all_situation_counts(game: Game, team_id: int) -> dict[str, int | float | None]:
    is_home = int(game.home_team_id) == int(team_id)
    gf = int(game.home_score if is_home else game.away_score or 0)
    ga = int(game.away_score if is_home else game.home_score or 0)
    sf = game.home_shots if is_home else game.away_shots
    sa = game.away_shots if is_home else game.home_shots
    sf_i = _team_stats_optional_int(sf)
    sa_i = _team_stats_optional_int(sa)
    pp_goals = _team_stats_optional_int(game.pp_goals_home if is_home else game.pp_goals_away)
    pp_opp = _team_stats_optional_int(game.pp_opp_home if is_home else game.pp_opp_away)
    pk_ga = _team_stats_optional_int(game.pp_goals_away if is_home else game.pp_goals_home)
    pk_opp = _team_stats_optional_int(game.pp_opp_away if is_home else game.pp_opp_home)
    pim_for = _team_stats_optional_int(game.pim_away if is_home else game.pim_home)
    pim_against = _team_stats_optional_int(game.pim_home if is_home else game.pim_away)
    hits_for = _team_stats_optional_int(game.hits_home if is_home else game.hits_away)
    hits_against = _team_stats_optional_int(game.hits_away if is_home else game.hits_home)
    hd_for = _team_stats_optional_sum(
        getattr(game, f"sq3_{'home' if is_home else 'away'}", None),
        getattr(game, f"sq4_{'home' if is_home else 'away'}", None),
    )
    hd_against = _team_stats_optional_sum(
        getattr(game, f"sq3_{'away' if is_home else 'home'}", None),
        getattr(game, f"sq4_{'away' if is_home else 'home'}", None),
    )
    hd_total = (hd_for + hd_against) if hd_for is not None and hd_against is not None else None
    shots_total = (sf_i + sa_i) if sf_i is not None and sa_i is not None else None
    pk_stops = max(0, pk_opp - pk_ga) if pk_opp is not None and pk_ga is not None else None
    return {
        "gf": gf,
        "ga": ga,
        "goal_diff": gf - ga,
        "goal_events": gf + ga,
        "sf": sf_i,
        "sa": sa_i,
        "shot_diff": _team_stats_optional_diff(sf_i, sa_i),
        "shots_total": shots_total,
        "pp_goals": pp_goals,
        "pp_opp": pp_opp,
        "pk_ga": pk_ga,
        "pk_opp": pk_opp,
        "pk_stops": pk_stops,
        "pim_for": pim_for,
        "pim_against": pim_against,
        "pim_diff": _team_stats_optional_diff(pim_for, pim_against),
        "hits_for": hits_for,
        "hits_against": hits_against,
        "hits_diff": _team_stats_optional_diff(hits_for, hits_against),
        "hd_for": hd_for,
        "hd_against": hd_against,
        "hd_diff": _team_stats_optional_diff(hd_for, hd_against),
        "hd_total": hd_total,
        "standings_pts": float(_game_points_for_team(game, team_id)),
    }


def _team_stats_situation_goal_counts(
    game: Game,
    team_id: int,
    situation: str,
    events: list[ScoringEvent],
) -> dict[str, int | float | None]:
    opp_id = int(game.away_team_id if int(game.home_team_id) == int(team_id) else game.home_team_id)
    gf = ga = 0
    for ev in events:
        if ev.scorer_player_id is None:
            continue
        bucket = _strength_situation_bucket(ev.strength)
        if bucket != situation:
            continue
        tid = ev.scoring_team_id
        if tid is None:
            continue
        if int(tid) == int(team_id):
            gf += 1
        elif int(tid) == opp_id:
            ga += 1
    return {
        "gf": gf,
        "ga": ga,
        "goal_diff": gf - ga,
        "goal_events": gf + ga,
    }


def _team_stats_game_counts(
    game: Game,
    team_id: int,
    situation: str,
    events_by_game: dict[int, list[ScoringEvent]],
) -> dict[str, int | float | None]:
    if situation == "all":
        return _team_stats_all_situation_counts(game, team_id)
    events = events_by_game.get(int(game.id), [])
    return _team_stats_situation_goal_counts(game, team_id, situation, events)


def _seasons_with_team_stats_trend_data(session: Session, team_id: int) -> list[Season]:
    season_ids = {
        int(x)
        for x in session.scalars(
            select(Game.season_id)
            .where(
                Game.status == "final",
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
            .distinct()
        ).all()
        if x is not None
    }
    if not season_ids:
        return []
    return list(
        session.scalars(
            select(Season)
            .where(Season.id.in_(season_ids))
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        ).all()
    )


def _team_stats_regular_game_limit(standing: TeamStanding | None, game_count: int) -> int:
    official_gp = int(getattr(standing, "gp", 0) or 0) if standing is not None else 0
    if official_gp <= 0 and standing is not None:
        official_gp = int(standing.standing_gp_display() or 0)
    return min(official_gp if official_gp > 0 else int(game_count or 0), 82)


def build_team_stats_trends_archive(
    session: Session,
    team: Team,
    *,
    default_season_id: int | None = None,
    default_segment: str = "rs",
) -> dict[str, Any]:
    from app.services.seasons import season_display_label

    team_id = int(team.id)
    seasons = _seasons_with_team_stats_trend_data(session, team_id)
    datasets: dict[str, dict[str, Any]] = {}
    season_options: list[dict[str, Any]] = []
    standings_by_season = {
        int(st.season_id): st
        for st in session.scalars(select(TeamStanding).where(TeamStanding.team_id == team_id)).all()
    }

    for season in seasons:
        season_key = int(season.id)
        season_has_data = False
        for segment in ("rs", "ps", "po"):
            games = session.scalars(
                select(Game)
                .where(
                    Game.season_id == season_key,
                    Game.status == "final",
                    or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
                    _team_player_trend_game_segment_filter(segment),
                )
                .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            ).all()
            if not games:
                continue
            if segment == "rs":
                standing = standings_by_season.get(season_key)
                game_limit = _team_stats_regular_game_limit(standing, len(games))
                games = games[:game_limit]
                if not games:
                    continue
            game_ids = [int(g.id) for g in games]
            events_by_game: dict[int, list[ScoringEvent]] = defaultdict(list)
            if game_ids:
                for ev in session.scalars(select(ScoringEvent).where(ScoringEvent.game_id.in_(game_ids))).all():
                    events_by_game[int(ev.game_id)].append(ev)

            for situation in ("all", "ev", "pp", "pk", "other"):
                series: list[dict[str, Any]] = []
                for idx, game in enumerate(games, start=1):
                    if game.home_score is None or game.away_score is None:
                        continue
                    counts = _team_stats_game_counts(game, team_id, situation, events_by_game)
                    series.append(
                        {
                            "date": game.game_date.isoformat() if game.game_date else None,
                            "game_number": idx,
                            "counts": counts,
                        }
                    )
                if not series:
                    continue
                datasets[f"{season_key}|{segment}|{situation}"] = {
                    "game_count": len(series),
                    "series": series,
                }
                season_has_data = True

        if season_has_data:
            season_options.append(
                {
                    "id": season_key,
                    "label": season_display_label(season),
                    "start_year": season.start_year,
                }
            )

    if default_season_id is None and season_options:
        default_season_id = int(season_options[0]["id"])
    if default_segment not in {s["key"] for s in TEAM_CHART_SEGMENTS}:
        default_segment = "rs"

    return {
        "team_id": team_id,
        "team_name": team.full_display_name(),
        "segments": TEAM_CHART_SEGMENTS,
        "situations": TEAM_STATS_TREND_SITUATIONS,
        "basis_options": TEAM_STATS_TREND_BASIS,
        "trend_modes": TEAM_STATS_TREND_MODES,
        "metrics": TEAM_STATS_TREND_METRICS,
        "seasons": season_options,
        "default_season_id": default_season_id,
        "default_segment": default_segment,
        "default_situation": "all",
        "default_basis": "totals",
        "default_trend_mode": "cumulative",
        "default_metric": "goal_diff",
        "rs_game_cap": 82,
        "datasets": datasets,
    }


def _game_points_for_team(game: Game, team_id: int) -> int:
    home_score = game.home_score
    away_score = game.away_score
    if home_score is None or away_score is None:
        return 0
    is_home = int(game.home_team_id) == int(team_id)
    team_score = int(home_score if is_home else away_score)
    opp_score = int(away_score if is_home else home_score)
    if team_score > opp_score:
        return 2
    if team_score == opp_score:
        return 1
    return 1 if game.went_to_overtime or game.went_to_shootout else 0


def build_points_above_ppg_divisions(session: Session, season_id: int) -> list[dict[str, Any]]:
    standings = session.scalars(
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season_id)
    ).all()
    if not standings:
        return []

    team_meta: dict[int, dict[str, Any]] = {}
    divisions: dict[str, list[int]] = defaultdict(list)
    for st in standings:
        tm = st.team
        if tm is None:
            continue
        tid = int(tm.id)
        division = (st.division or st.conference or "League").strip() or "League"
        team_meta[tid] = {"team": tm, "division": division}
        divisions[division].append(tid)

    if not team_meta:
        return []

    games = session.scalars(
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
        .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
    ).all()
    points_by_team: dict[int, int] = defaultdict(int)
    gp_by_team: dict[int, int] = defaultdict(int)
    series_by_team: dict[int, list[dict[str, float | int]]] = defaultdict(list)

    for game in games:
        game_type = str(game.game_type or "").lower()
        if game_type and "playoff" in game_type:
            continue
        for tid in (int(game.home_team_id), int(game.away_team_id)):
            if tid not in team_meta:
                continue
            gp_by_team[tid] += 1
            points_by_team[tid] += _game_points_for_team(game, tid)
            gp = gp_by_team[tid]
            series_by_team[tid].append(
                {"gp": gp, "value": float(points_by_team[tid] - gp)}
            )

    out: list[dict[str, Any]] = []
    for division in sorted(divisions):
        teams: list[dict[str, Any]] = []
        for tid in sorted(
            divisions[division],
            key=lambda t: (
                -float(series_by_team[t][-1]["value"] if series_by_team[t] else 0),
                str(team_meta[t]["team"].abbreviation or team_meta[t]["team"].name or ""),
            ),
        ):
            tm = team_meta[tid]["team"]
            series = series_by_team.get(tid, [])
            teams.append(
                {
                    "team": tm,
                    "points": series,
                    "final_value": series[-1]["value"] if series else 0,
                    "max_gp": series[-1]["gp"] if series else 0,
                }
            )
        if teams:
            out.append({"division": division, "teams": teams})
    return out


def build_luck_sustainability_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> list[dict[str, Any]]:
    min_gp = _adaptive_min_gp(session, PlayerSkaterStat, season_id, segment, MIN_SKATER_GP)
    skaters = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player), joinedload(PlayerSkaterStat.team))
        .where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
            PlayerSkaterStat.gp >= min_gp,
        )
    ).all()
    out: list[dict[str, Any]] = []
    for st in skaters:
        pl = st.player
        if pl is None or st.pdo is None:
            continue
        goals = int(st.goals or 0)
        shots = int(st.shots or 0)
        exp_goals_proxy = round(shots * 0.09, 1) if shots else None
        gap = round(goals - exp_goals_proxy, 1) if exp_goals_proxy is not None else None
        out.append(
            {
                "player_id": int(pl.id),
                "player_name": pl.full_name,
                "team": st.team,
                "pdo": st.pdo,
                "pdo_band": pdo_band(st.pdo),
                "goals": goals,
                "shots": shots,
                "goals_vs_shots_proxy": gap,
            }
        )
    return out


def build_discipline_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> list[dict[str, Any]]:
    def _context_for_players(player_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not player_ids:
            return {}
        ctx: dict[int, dict[str, Any]] = defaultdict(dict)
        stat_rows = session.scalars(
            select(PlayerSkaterStat)
            .options(joinedload(PlayerSkaterStat.team))
            .where(
                PlayerSkaterStat.season_id == season_id,
                PlayerSkaterStat.stat_segment == segment,
                PlayerSkaterStat.player_id.in_(player_ids),
            )
        ).all()
        for st in stat_rows:
            ctx[int(st.player_id)]["team"] = st.team
            ctx[int(st.player_id)]["gp"] = int(st.gp or 0)

        game_rows = session.execute(
            select(
                GameSkaterStat.player_id,
                GameSkaterStat.team_id,
                func.count(func.distinct(GameSkaterStat.game_id)),
            )
            .join(Game, Game.id == GameSkaterStat.game_id)
            .where(Game.season_id == season_id, GameSkaterStat.player_id.in_(player_ids))
            .group_by(GameSkaterStat.player_id, GameSkaterStat.team_id)
        ).all()
        team_ids = {int(r[1]) for r in game_rows if r[1] is not None}
        teams = {
            int(t.id): t for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        }
        for pid, team_id, gp in game_rows:
            if pid is None:
                continue
            rec = ctx[int(pid)]
            if "gp" not in rec:
                rec["gp"] = int(gp or 0)
            if "team" not in rec and team_id is not None:
                rec["team"] = teams.get(int(team_id))

        penalty_team_rows = session.execute(
            select(
                PenaltyEvent.player_id,
                PenaltyEvent.team_id,
                func.count(PenaltyEvent.id),
            )
            .join(Game, Game.id == PenaltyEvent.game_id)
            .where(
                Game.season_id == season_id,
                PenaltyEvent.player_id.in_(player_ids),
                PenaltyEvent.team_id.is_not(None),
            )
            .group_by(PenaltyEvent.player_id, PenaltyEvent.team_id)
            .order_by(PenaltyEvent.player_id, func.count(PenaltyEvent.id).desc())
        ).all()
        penalty_team_ids = {int(r[1]) for r in penalty_team_rows if r[1] is not None}
        if penalty_team_ids - set(teams):
            teams.update(
                {
                    int(t.id): t
                    for t in session.scalars(select(Team).where(Team.id.in_(penalty_team_ids))).all()
                }
            )
        seen_penalty_team: set[int] = set()
        for pid, team_id, _cnt in penalty_team_rows:
            if pid is None or int(pid) in seen_penalty_team:
                continue
            seen_penalty_team.add(int(pid))
            rec = ctx[int(pid)]
            if "team" not in rec and team_id is not None:
                rec["team"] = teams.get(int(team_id))

        return ctx

    rows = session.execute(
        select(
            PenaltyEvent.player_id,
            func.count(PenaltyEvent.id),
            func.sum(PenaltyEvent.minutes),
        )
        .join(Game, Game.id == PenaltyEvent.game_id)
        .where(Game.season_id == season_id, PenaltyEvent.player_id.is_not(None))
        .group_by(PenaltyEvent.player_id)
    ).all()
    if not rows:
        fallback = session.execute(
            select(
                GameSkaterStat.player_id,
                func.sum(GameSkaterStat.pim),
            )
            .join(Game, Game.id == GameSkaterStat.game_id)
            .where(Game.season_id == season_id, GameSkaterStat.pim > 0)
            .group_by(GameSkaterStat.player_id)
        ).all()
        if not fallback:
            return []
        player_ids = [int(r[0]) for r in fallback if r[0] is not None]
        context = _context_for_players(player_ids)
        players = {
            int(p.id): p
            for p in session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
        }
        out = []
        for pid, mins in fallback:
            if pid is None:
                continue
            pl = players.get(int(pid))
            if pl is None:
                continue
            out.append(
                {
                    "player_id": int(pid),
                    "player_name": pl.full_name,
                    "team": context.get(int(pid), {}).get("team"),
                    "gp": context.get(int(pid), {}).get("gp"),
                    "penalties": None,
                    "pim": int(mins or 0),
                    "source": "game_pim",
                }
            )
        out.sort(key=lambda r: -int(r["pim"]))
        return out
    player_ids = [int(r[0]) for r in rows if r[0] is not None]
    context = _context_for_players(player_ids)
    players = {
        int(p.id): p
        for p in session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    }
    out: list[dict[str, Any]] = []
    for pid, cnt, mins in rows:
        if pid is None:
            continue
        pl = players.get(int(pid))
        if pl is None:
            continue
        out.append(
            {
                "player_id": int(pid),
                "player_name": pl.full_name,
                "team": context.get(int(pid), {}).get("team"),
                "gp": context.get(int(pid), {}).get("gp"),
                "penalties": int(cnt or 0),
                "pim": int(mins or 0),
            }
        )
    out.sort(key=lambda r: (-int(r["penalties"]), -int(r["pim"])))
    return out


def build_player_process_profile(
    session: Session,
    player: Player,
    season_id: int,
    *,
    is_goalie: bool,
    segment: str = "rs",
) -> dict[str, Any] | None:
    if is_goalie:
        st = session.scalars(
            select(PlayerGoalieStat).where(
                PlayerGoalieStat.season_id == season_id,
                PlayerGoalieStat.player_id == player.id,
                PlayerGoalieStat.stat_segment == segment,
            ).limit(1)
        ).first()
        if not st:
            return None
        league_sv = _league_goalie_sv_pct(
            list(
                session.scalars(
                    select(PlayerGoalieStat).where(
                        PlayerGoalieStat.season_id == season_id,
                        PlayerGoalieStat.stat_segment == segment,
                    )
                ).all()
            )
        )
        game_lines = session.scalars(
            select(GameGoalieStat)
            .join(Game, Game.id == GameGoalieStat.game_id)
            .where(
                GameGoalieStat.player_id == player.id,
                Game.season_id == season_id,
                Game.status == "final",
            )
        ).all()
        season = _goalie_season_process_snapshot(st, league_sv)
        game_log = _goalie_game_log_profile(game_lines)
        if season.get("game_rating") is None and game_log.get("avg_game_rating") is not None:
            season["game_rating"] = game_log["avg_game_rating"]
            season["game_rating_source"] = "game_logs"
        recent_10 = _recent_goalie_window(session, player.id, 10)
        recent_20 = _recent_goalie_window(session, player.id, 20)
        return {
            "kind": "goalie",
            "season": season,
            "game_log": game_log,
            "rolling": {"last_10": recent_10, "last_20": recent_20},
        }

    st = session.scalars(
        select(PlayerSkaterStat).where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.player_id == player.id,
            PlayerSkaterStat.stat_segment == segment,
        ).limit(1)
    ).first()
    if not st:
        return None
    game_lines = session.scalars(
        select(GameSkaterStat)
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(
            GameSkaterStat.player_id == player.id,
            Game.season_id == season_id,
            Game.status == "final",
        )
    ).all()
    oz = sum(int(l.oz_starts or 0) for l in game_lines)
    nz = sum(int(l.nz_starts or 0) for l in game_lines)
    dz = sum(int(l.dz_starts or 0) for l in game_lines)
    sq_counts = {k: sum(int(getattr(l, k) or 0) for l in game_lines) for k in SQ_KEYS}
    penalties = session.scalar(
        select(func.count(PenaltyEvent.id))
        .join(Game, Game.id == PenaltyEvent.game_id)
        .where(Game.season_id == season_id, PenaltyEvent.player_id == player.id)
    )
    pim = session.scalar(
        select(func.coalesce(func.sum(PenaltyEvent.minutes), 0))
        .join(Game, Game.id == PenaltyEvent.game_id)
        .where(Game.season_id == season_id, PenaltyEvent.player_id == player.id)
    )
    season = _skater_season_process_snapshot(st)
    game_events = _skater_game_event_profile(game_lines)
    if game_events.get("missed_shots") is not None and int(game_events.get("missed_shots") or 0) > 0:
        season["missed_shots"] = game_events["missed_shots"]
    rolling_10 = _aggregate_game_skater_lines(
        session, player.id, _recent_game_ids_for_player(session, player.id, window=10)
    )
    rolling_20 = _aggregate_game_skater_lines(
        session, player.id, _recent_game_ids_for_player(session, player.id, window=20)
    )
    if season.get("sf_per_60") is None and rolling_10.get("sf_per_60") is not None:
        season["sf_per_60"] = rolling_10["sf_per_60"]
        season["sf_per_60_source"] = "last_10"
    return {
        "kind": "skater",
        "season": season,
        "shot_share": {
            "cf": season.get("cf"),
            "ca": season.get("ca"),
            "ff": season.get("ff"),
            "fa": season.get("fa"),
            "cf_pct_rel": season.get("cf_pct_rel"),
            "ff_pct_rel": season.get("ff_pct_rel"),
        },
        "game_events": game_events,
        "zone_starts": zone_start_pcts(oz, nz, dz),
        "sq": sq_profile_from_counts(sq_counts),
        "discipline": {"penalties": int(penalties or 0), "pim": int(pim or 0)},
        "rolling": {"last_10": rolling_10, "last_20": rolling_20},
    }


def _recent_goalie_window(session: Session, player_id: int, window: int) -> dict[str, Any]:
    lines = session.scalars(
        select(GameGoalieStat)
        .join(Game, Game.id == GameGoalieStat.game_id)
        .where(GameGoalieStat.player_id == player_id, Game.status == "final")
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(window)
    ).all()
    if not lines:
        return {}
    sa = sum(int(l.shots_against or 0) for l in lines)
    ga = sum(int(l.goals_allowed or 0) for l in lines)
    saves = sum(int(l.saves or 0) for l in lines)
    toi_seconds = sum(int(l.toi_seconds or 0) for l in lines)
    ratings = [float(l.game_rating) for l in lines if l.game_rating is not None]
    sv = (sa - ga) / sa if sa > 0 else None
    gaa = round(ga / (toi_seconds / 3600.0), 2) if toi_seconds > 0 else None
    return {
        "gp": len(lines),
        "sa": sa,
        "ga": ga,
        "saves": saves if saves > 0 else (max(0, sa - ga) if sa > 0 else 0),
        "sv_pct": round(sv, 3) if sv is not None else None,
        "gaa": gaa,
        "avg_game_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "shutouts": sum(
            1 for l in lines if int(l.goals_allowed or 0) == 0 and int(l.shots_against or 0) > 0
        ),
    }


def build_process_momentum_payload(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
    limit: int = 5,
    logo_season_year: int | None = None,
    player_photo_url: Any | None = None,
) -> dict[str, Any]:
    from app.services.season_team_logo_bundle import dashboard_team_logo_url

    def _player_photo(pl: Player) -> str:
        return str(player_photo_url(pl) if callable(player_photo_url) else "")

    def _team_fields(team_id: int | None, pl: Player) -> dict[str, str]:
        tm = session.get(Team, team_id) if team_id else None
        if tm is None and pl.current_team_id:
            tm = session.get(Team, int(pl.current_team_id))
        if tm is None:
            return {"team": "", "team_slug": "", "team_logo_url": ""}
        return {
            "team": str(tm.abbreviation or ""),
            "team_slug": str(tm.slug or ""),
            "team_logo_url": dashboard_team_logo_url(tm, logo_season_year),
        }

    def _recent_goalie_team_id(player_id: int) -> int | None:
        team_id = session.scalar(
            select(GameGoalieStat.team_id)
            .join(Game, Game.id == GameGoalieStat.game_id)
            .where(GameGoalieStat.player_id == player_id, Game.status == "final")
            .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
            .limit(1)
        )
        return int(team_id) if team_id else None

    min_skater_gp = _adaptive_min_gp(session, PlayerSkaterStat, season_id, segment, MIN_SKATER_GP)
    skaters = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player))
        .where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
            PlayerSkaterStat.gp >= min_skater_gp,
            PlayerSkaterStat.cf_pct.is_not(None),
        )
    ).all()
    skater_movers: list[dict[str, Any]] = []
    for st in skaters:
        pl = st.player
        if pl is None:
            continue
        recent = _aggregate_game_skater_lines(
            session,
            int(pl.id),
            _recent_game_ids_for_player(session, int(pl.id), window=10),
        )
        if not recent:
            continue
        skater_movers.append(
            {
                "player_id": int(pl.id),
                "player_name": pl.full_name,
                "player_photo_url": _player_photo(pl),
                **_team_fields(recent.get("team_id") or st.team_id, pl),
                "season_cf_pct": st.cf_pct,
                "recent_goals": recent.get("goals"),
                "recent_shots": recent.get("shots"),
                "recent_gp": recent.get("gp"),
            }
        )
    skater_movers.sort(key=lambda r: (-(r.get("recent_goals") or 0), -(r.get("recent_shots") or 0)))
    min_goalie_gp = _adaptive_min_gp(session, PlayerGoalieStat, season_id, segment, MIN_GOALIE_GP)
    goalies = session.scalars(
        select(PlayerGoalieStat)
        .options(joinedload(PlayerGoalieStat.player))
        .where(
            PlayerGoalieStat.season_id == season_id,
            PlayerGoalieStat.stat_segment == segment,
            PlayerGoalieStat.gp >= min_goalie_gp,
        )
    ).all()
    league_sv_pct = _league_goalie_sv_pct(goalies)
    goalie_rows = []
    for st in goalies:
        pl = st.player
        gsaa = _estimated_goalie_gsaa(st, league_sv_pct)
        if pl is None or gsaa is None:
            continue
        goalie_rows.append(
            {
                "player_id": int(pl.id),
                "player_name": pl.full_name,
                "player_photo_url": _player_photo(pl),
                **_team_fields(st.team_id or _recent_goalie_team_id(int(pl.id)), pl),
                "gsaa": gsaa,
                "sv_pct": st.sv_pct,
            }
        )
    goalie_rows.sort(key=lambda r: float(r["gsaa"] or 0), reverse=True)
    return {
        "skaters": skater_movers[:limit],
        "goalies": goalie_rows[:limit],
    }


def build_game_flow_card(session: Session, game: Game) -> dict[str, Any] | None:
    if game.status != "final":
        return None
    home_sq = sq_profile_from_counts({k: int(getattr(game, f"{k}_home") or 0) for k in SQ_KEYS})
    away_sq = sq_profile_from_counts({k: int(getattr(game, f"{k}_away") or 0) for k in SQ_KEYS})
    scoring = session.scalars(
        select(ScoringEvent)
        .where(ScoringEvent.game_id == game.id)
        .order_by(ScoringEvent.period, ScoringEvent.time_elapsed)
    ).all()
    timeline = [
        {
            "period": ev.period,
            "time": ev.time_elapsed,
            "team_id": ev.scoring_team_id,
        }
        for ev in scoring
    ]
    return {
        "game_id": int(game.id),
        "period_scores": {
            "home": [
                game.score_home_p1,
                game.score_home_p2,
                game.score_home_p3,
                game.score_home_ot,
            ],
            "away": [
                game.score_away_p1,
                game.score_away_p2,
                game.score_away_p3,
                game.score_away_ot,
            ],
        },
        "sog_by_period": {
            "home": [game.sog_home_p1, game.sog_home_p2, game.sog_home_p3, game.sog_home_ot],
            "away": [game.sog_away_p1, game.sog_away_p2, game.sog_away_p3, game.sog_away_ot],
        },
        "sq_home": home_sq,
        "sq_away": away_sq,
        "scoring_timeline": timeline,
        "pp": {
            "home": {"goals": game.pp_goals_home, "opps": game.pp_opp_home},
            "away": {"goals": game.pp_goals_away, "opps": game.pp_opp_away},
        },
    }


def build_advanced_stats_hub_payload(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
) -> dict[str, Any]:
    return {
        "segment": segment,
        "skaters": build_skater_leaderboard_rows(session, season_id, segment=segment),
        "goalies": build_goalie_leaderboard_rows(session, season_id, segment=segment),
        "teams": build_team_process_rows(session, season_id, segment=segment),
        "points_above_ppg": build_points_above_ppg_divisions(session, season_id),
        "luck": build_luck_sustainability_rows(session, season_id, segment=segment),
        "discipline": build_discipline_rows(session, season_id, segment=segment),
    }


def _team_json(team: Team | None) -> dict[str, Any] | None:
    if team is None:
        return None
    return {
        "id": int(team.id),
        "name": team.name,
        "abbreviation": team.abbreviation,
        "slug": team.slug,
    }


def build_advanced_stats_hub_json(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
    team_id: int | None = None,
    line_type: str = "all",
    min_combined_gp: int = 0,
    min_combined_toi_seconds: int = 0,
) -> dict[str, Any]:
    from app.services.line_stats import build_line_stats_json_rows, build_line_stats_rows

    hub = build_advanced_stats_hub_payload(session, season_id, segment=segment)
    line_rows = build_line_stats_rows(
        session,
        season_id,
        segment=segment,
        team_id=team_id,
        line_type=line_type,
        min_combined_gp=min_combined_gp,
        min_combined_toi_seconds=min_combined_toi_seconds,
    )
    out = dict(hub)
    out["lines"] = build_line_stats_json_rows(line_rows)
    out["skaters"] = [
        {**row, "team": _team_json(row.get("team"))} for row in hub.get("skaters", [])
    ]
    out["goalies"] = [
        {**row, "team": _team_json(row.get("team"))} for row in hub.get("goalies", [])
    ]
    out["teams"] = [
        {
            **{k: v for k, v in row.items() if k != "team"},
            "team": _team_json(row.get("team")),
        }
        for row in hub.get("teams", [])
    ]
    out["points_above_ppg"] = [
        {
            "division": group.get("division"),
            "teams": [
                {
                    **{k: v for k, v in row.items() if k != "team"},
                    "team": _team_json(row.get("team")),
                }
                for row in group.get("teams", [])
            ],
        }
        for group in hub.get("points_above_ppg", [])
    ]
    out["luck"] = [
        {**row, "team": _team_json(row.get("team"))} for row in hub.get("luck", [])
    ]
    out["discipline"] = [
        {**row, "team": _team_json(row.get("team"))} for row in hub.get("discipline", [])
    ]
    return out
