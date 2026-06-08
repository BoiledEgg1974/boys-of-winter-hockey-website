"""Process-over-results analytics from imported FHM stats (not xG)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
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
        select(GameSkaterStat).where(
            GameSkaterStat.player_id == player_id,
            GameSkaterStat.game_id.in_(game_ids),
        )
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
    sq = sq_profile_from_counts(sq_counts)
    points = goals + assists
    return {
        "gp": len(game_ids),
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
) -> dict[str, Any]:
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
    goalie_rows = [
        {
            "player_id": int(st.player.id),
            "player_name": st.player.full_name,
            "gsaa": _estimated_goalie_gsaa(st, league_sv_pct),
            "sv_pct": st.sv_pct,
        }
        for st in goalies
        if st.player is not None and _estimated_goalie_gsaa(st, league_sv_pct) is not None
    ]
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
