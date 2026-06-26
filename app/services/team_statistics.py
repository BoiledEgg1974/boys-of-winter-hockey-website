"""Team Statistics page — league-wide team stats, filters, and chart archive."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game,
    PlayerSkaterStat,
    ScoringEvent,
    Season,
    Team,
    TeamSeasonAggregate,
    TeamStanding,
)
from app.services.advanced_stats import (
    TEAM_CHART_SEGMENTS,
    _pk_pct,
    _pp_pct,
    _team_player_trend_game_segment_filter,
    _team_stats_game_counts,
    _team_sq_totals_from_games,
    build_team_analytics_chart_archive,
    sq_profile_from_counts,
)
from app.services.seasons import season_display_label
from app.services.standings import standings_for_season, team_aggregate_rows

STRENGTH_OPTIONS: list[dict[str, str]] = [
    {"key": "all", "label": "All Situations"},
    {"key": "ev", "label": "5 on 5"},
    {"key": "pp", "label": "5 on 4"},
    {"key": "pk", "label": "4 on 5"},
    {"key": "other", "label": "Other"},
]

TABLE_COLUMNS: list[dict[str, Any]] = [
    {"key": "team", "label": "Team", "abbr": "Team", "sort": "str", "situations": ["all", "ev", "pp", "pk", "other"]},
    {"key": "gf", "label": "Goals For", "abbr": "GF", "sort": "num", "situations": ["all", "ev", "pp", "pk", "other"]},
    {"key": "ga", "label": "Goals Against", "abbr": "GA", "sort": "num", "situations": ["all", "ev", "pp", "pk", "other"]},
    {"key": "diff", "label": "Diff", "abbr": "Diff", "sort": "num", "situations": ["all", "ev", "pp", "pk", "other"]},
    {"key": "sf", "label": "Shots For", "abbr": "SF", "sort": "num", "situations": ["all"]},
    {"key": "sa", "label": "Shots Against", "abbr": "SA", "sort": "num", "situations": ["all"]},
    {"key": "fo_pct", "label": "Faceoff %", "abbr": "FO%", "sort": "num", "situations": ["all"]},
    {"key": "bs", "label": "Blocked Shots", "abbr": "BS", "sort": "num", "situations": ["all"]},
    {"key": "hit", "label": "Hits", "abbr": "HIT", "sort": "num", "situations": ["all"]},
    {"key": "tka", "label": "Takeaways", "abbr": "TKA", "sort": "num", "situations": ["all"]},
    {"key": "gva", "label": "Giveaways", "abbr": "GVA", "sort": "num", "situations": ["all"]},
    {"key": "pp_ch", "label": "PP Chances", "abbr": "PP Ch", "sort": "num", "situations": ["all"]},
    {"key": "ppg", "label": "PP Goals", "abbr": "PPG", "sort": "num", "situations": ["all"]},
    {"key": "pp_pct", "label": "Power Play %", "abbr": "PP%", "sort": "num", "situations": ["all"]},
    {"key": "pk_ga", "label": "PK Goals Against", "abbr": "PK GA", "sort": "num", "situations": ["all"]},
    {"key": "sh_ch", "label": "SH Chances", "abbr": "SH Ch", "sort": "num", "situations": ["all"]},
    {"key": "pk_pct", "label": "Penalty Kill %", "abbr": "PK%", "sort": "num", "situations": ["all"]},
    {"key": "shg", "label": "SH Goals", "abbr": "SHG", "sort": "num", "situations": ["all"]},
    {"key": "pim_g", "label": "PIM per Game", "abbr": "PIM/G", "sort": "num", "situations": ["all"]},
    {"key": "att_h", "label": "Avg Home Attendance", "abbr": "ATT H", "sort": "num", "situations": ["all"]},
    {"key": "cap_pct", "label": "Capacity %", "abbr": "CAP %", "sort": "num", "situations": ["all"]},
    {"key": "cf_pct", "label": "CF%", "abbr": "CF%", "sort": "num", "situations": ["all"]},
    {"key": "ff_pct", "label": "FF%", "abbr": "FF%", "sort": "num", "situations": ["all"]},
    {"key": "gf_per_60", "label": "GF/60", "abbr": "GF/60", "sort": "num", "situations": ["all"]},
    {"key": "ga_per_60", "label": "GA/60", "abbr": "GA/60", "sort": "num", "situations": ["all"]},
    {"key": "sq_hd", "label": "High-Danger SQ %", "abbr": "SQ HD%", "sort": "num", "situations": ["all"]},
]

CARD_METRICS: list[dict[str, str]] = [
    {"key": "point_pct", "label": "Points %"},
    {"key": "gf_g", "label": "GF/G"},
    {"key": "ga_g", "label": "GA/G"},
    {"key": "pp_pct", "label": "PP%"},
    {"key": "pk_pct", "label": "PK%"},
    {"key": "shot_diff", "label": "Shot Diff"},
    {"key": "cf_pct", "label": "CF%"},
    {"key": "sq_hd", "label": "High-Danger SQ%"},
]

RATE_COUNT_KEYS = frozenset(
    {"gp", "w", "l", "pts", "gf", "ga", "diff", "sf", "sa", "bs", "hit", "tka", "gva", "pp_ch", "ppg", "pk_ga", "sh_ch", "shg"}
)


def rank_maps_for_segment(session: Session, season_id: int, segment: str) -> dict[int, dict[str, int]]:
    """League-wide dense ranks per stat key for TeamSeasonAggregate rows in one segment."""
    aggs = session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season_id,
            TeamSeasonAggregate.stat_segment == segment,
        )
    ).all()
    if not aggs:
        return {}

    specs = {
        "shots_for": ("shots_for", True),
        "shots_against": ("shots_against", False),
        "faceoff_pct": ("faceoff_pct", True),
        "blocked_shots": ("blocked_shots", True),
        "hits": ("hits", True),
        "takeaways": ("takeaways", True),
        "giveaways": ("giveaways", False),
        "pp_chances": ("pp_chances", True),
        "pp_goals": ("pp_goals", True),
        "pp_pct": ("pp_pct", True),
        "pk_goals_against": ("pk_goals_against", False),
        "sh_chances": ("sh_chances", False),
        "pk_pct": ("pk_pct", True),
        "sh_goals": ("sh_goals", True),
        "pim_per_game": ("pim_per_game", False),
        "attendance_home": ("attendance_home", True),
    }

    by_team: dict[int, dict[str, int]] = {}
    for key, (attr, high_good) in specs.items():
        vals: list[tuple[int, float]] = []
        for a in aggs:
            if attr == "pp_pct":
                if a.pp_chances and a.pp_chances > 0 and a.pp_goals is not None:
                    v = float(a.pp_goals) / float(a.pp_chances)
                else:
                    v = None
            elif attr == "pk_pct":
                if a.sh_chances and a.sh_chances > 0 and a.pk_goals_against is not None:
                    v = 100.0 - (100.0 * float(a.pk_goals_against) / float(a.sh_chances))
                else:
                    v = None
            else:
                raw = getattr(a, attr)
                v = float(raw) if raw is not None else None
            if v is None or a.team_id is None:
                continue
            vals.append((int(a.team_id), v))
        if not vals:
            continue
        vals.sort(key=lambda tv: tv[1], reverse=high_good)
        prev_val: float | None = None
        rank = 0
        for idx, (tid, v) in enumerate(vals, start=1):
            if prev_val is None or abs(v - prev_val) > 1e-12:
                rank = idx
                prev_val = v
            by_team.setdefault(tid, {})[key] = rank
    return by_team


def _toi_weighted_avg(rows: list[PlayerSkaterStat], attr: str) -> float | None:
    total_toi = 0
    weighted = 0.0
    decimals = 2 if attr.endswith("_per_60") else 1
    for st in rows:
        toi = int(st.toi_seconds or 0)
        val = getattr(st, attr, None)
        if toi <= 0 or val is None:
            continue
        total_toi += toi
        weighted += float(val) * toi
    if total_toi <= 0:
        return None
    return round(weighted / total_toi, decimals)


def _team_skater_process_metrics(
    session: Session,
    season_id: int,
    team_id: int,
    segment: str,
) -> dict[str, float | None]:
    skaters = session.scalars(
        select(PlayerSkaterStat).where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.team_id == team_id,
            PlayerSkaterStat.stat_segment == segment,
        )
    ).all()
    if not skaters:
        return {"cf_pct": None, "ff_pct": None, "gf_per_60": None, "ga_per_60": None}
    return {
        "cf_pct": _toi_weighted_avg(skaters, "cf_pct"),
        "ff_pct": _toi_weighted_avg(skaters, "ff_pct"),
        "gf_per_60": _toi_weighted_avg(skaters, "gf_per_60"),
        "ga_per_60": _toi_weighted_avg(skaters, "ga_per_60"),
    }


def _situational_goal_totals(
    session: Session,
    season_id: int,
    segment: str,
    situation: str,
) -> dict[int, dict[str, int | None]]:
    games = session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status == "final",
            _team_player_trend_game_segment_filter(segment),
        )
    ).all()
    if not games:
        return {}
    game_ids = [int(g.id) for g in games]
    events_by_game: dict[int, list[ScoringEvent]] = defaultdict(list)
    if situation != "all" and game_ids:
        for ev in session.scalars(select(ScoringEvent).where(ScoringEvent.game_id.in_(game_ids))).all():
            events_by_game[int(ev.game_id)].append(ev)

    team_ids = {
        int(tid)
        for g in games
        for tid in (g.home_team_id, g.away_team_id)
        if tid is not None
    }
    out: dict[int, dict[str, int | None]] = {}
    for tid in team_ids:
        gf = ga = 0
        gp = 0
        for game in games:
            if int(game.home_team_id) != tid and int(game.away_team_id) != tid:
                continue
            if game.home_score is None or game.away_score is None:
                continue
            gp += 1
            counts = _team_stats_game_counts(game, tid, situation, events_by_game)
            gf += int(counts.get("gf") or 0)
            ga += int(counts.get("ga") or 0)
        if gp > 0:
            out[tid] = {"gf": gf, "ga": ga, "diff": gf - ga, "gp": gp}
    return out


def _rank_for_values(
    rows: list[dict[str, Any]],
    key: str,
    value: float | int | None,
    high_good: bool,
) -> int | None:
    if value is None:
        return None
    ordered = sorted(
        [r.get(key) for r in rows if r.get(key) is not None],
        reverse=high_good,
    )
    if not ordered:
        return None
    prev: float | None = None
    rank = 0
    value_map: dict[float, int] = {}
    for idx, v in enumerate(ordered, start=1):
        fv = float(v)
        if prev is None or abs(fv - prev) > 1e-12:
            rank = idx
            prev = fv
        value_map[fv] = rank
    return value_map.get(float(value))


def _seasons_with_team_data(session: Session) -> list[Season]:
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


def format_rate_value(
    key: str,
    value: float | int | None,
    *,
    gp: int | None,
    rate: str,
) -> float | int | None:
    if value is None:
        return None
    if rate == "raw" or key not in RATE_COUNT_KEYS:
        return value
    if not gp or gp <= 0:
        return None
    num = float(value)
    if rate == "per_game":
        return round(num / gp, 2)
    if rate == "per_60":
        return round((num / gp) * 60.0, 2)
    if rate == "per_82":
        return round(num * 82.0 / gp, 2)
    return value


def build_team_statistics_chart_archive(
    session: Session,
    *,
    default_season_id: int | None = None,
    default_segment: str = "rs",
    season_label: str | None = None,
) -> dict[str, Any]:
    archive = build_team_analytics_chart_archive(
        session,
        default_season_id=default_season_id,
        default_segment=default_segment,
    )
    archive["default_x"] = "ga"
    archive["default_y"] = "gf"
    archive["default_norm"] = "per_game"
    archive["season_label"] = season_label
    archive["invert_x_for_low_better"] = True

    extra_metrics = [
        {"key": "cf_pct", "label": "CF%", "per_game": False, "per_60": False, "decimals": 1, "better": "high"},
        {"key": "ff_pct", "label": "FF%", "per_game": False, "per_60": False, "decimals": 1, "better": "high"},
        {"key": "gf_per_60", "label": "GF/60", "per_game": False, "per_60": False, "decimals": 2, "better": "high"},
        {"key": "ga_per_60", "label": "GA/60", "per_game": False, "per_60": False, "decimals": 2, "better": "low"},
    ]
    existing = {m["key"] for m in archive.get("metrics", [])}
    for m in extra_metrics:
        if m["key"] not in existing:
            archive.setdefault("metrics", []).append(m)

    for ds_key, ds in archive.get("datasets", {}).items():
        parts = ds_key.split("|")
        if len(parts) < 2:
            continue
        season_id = int(parts[0])
        segment = parts[1]
        for team_row in ds.get("teams", []):
            tid = int(team_row["team_id"])
            proc = _team_skater_process_metrics(session, season_id, tid, segment)
            metrics = team_row.setdefault("metrics", {})
            metrics.update(proc)
            if season_label:
                team_row["season_label"] = season_label

    archive["norm_options"] = [
        {"key": "raw", "label": "Season totals / rates"},
        {"key": "per_game", "label": "Per game"},
        {"key": "per_60", "label": "Per 60"},
    ]
    return archive


def build_team_statistics_page_payload(
    session: Session,
    *,
    season: Season,
    segment: str = "rs",
    strength: str = "all",
    rate: str = "raw",
    team_slugs: list[str] | None = None,
    standings_rows: list | None = None,
) -> dict[str, Any]:
    from app.services.season_team_logo_bundle import get_season_team_logo_bundle

    if standings_rows is None:
        standings_rows = standings_for_season(season)

    stat_rows = team_aggregate_rows(season, standings_rows, segment)
    standings_by_team = {int(st.team_id): st for st in standings_rows}
    rank_maps = rank_maps_for_segment(session, int(season.id), segment)
    situational = (
        _situational_goal_totals(session, int(season.id), segment, strength)
        if strength != "all"
        else {}
    )
    logo_bundle = get_season_team_logo_bundle()
    logo_sy = season.start_year

    slug_filter: set[str] | None = None
    if team_slugs:
        slug_filter = {s.strip().lower() for s in team_slugs if s.strip()}

    table_rows: list[dict[str, Any]] = []

    for team, agg, home_gp, cap_pct in stat_rows:
        if team is None:
            continue
        if slug_filter and (team.slug or "").lower() not in slug_filter:
            continue
        tid = int(team.id)
        st = standings_by_team.get(tid)
        gp = int(st.gp or 0) if st else None
        proc = _team_skater_process_metrics(session, int(season.id), tid, segment)
        sq = sq_profile_from_counts(_team_sq_totals_from_games(session, int(season.id), tid))
        ranks = rank_maps.get(tid, {})

        if strength != "all" and tid in situational:
            sit = situational[tid]
            gf = sit.get("gf")
            ga = sit.get("ga")
            diff = sit.get("diff")
            gp = sit.get("gp") or gp
        else:
            gf = int(st.gf or 0) if st and st.gf is not None else None
            ga = int(st.ga or 0) if st and st.ga is not None else None
            diff = (gf - ga) if gf is not None and ga is not None else None

        pp_pct = _pp_pct(agg.pp_goals, agg.pp_chances) if agg else None
        pk_pct = _pk_pct(agg.pk_goals_against, agg.sh_chances) if agg else None
        avg_att_h = (
            (agg.attendance_home / home_gp)
            if agg and agg.attendance_home is not None and home_gp and home_gp > 0
            else None
        )
        pts = int(st.pts) if st and st.pts is not None else None
        point_pct = round(100.0 * pts / (gp * 2), 1) if pts is not None and gp and gp > 0 else None
        shot_diff = (
            (agg.shots_for - agg.shots_against)
            if agg and agg.shots_for is not None and agg.shots_against is not None
            else None
        )

        table_rows.append(
            {
                "team": team,
                "team_id": tid,
                "slug": team.slug,
                "logo_url": logo_bundle.team_logo_url_for_season_context(team, logo_sy),
                "primary_color": team.primary_color,
                "gp": gp,
                "w": int(st.w) if st else None,
                "l": int(st.l) if st else None,
                "pts": pts,
                "point_pct": point_pct,
                "gf": gf,
                "ga": ga,
                "diff": diff,
                "sf": agg.shots_for if agg else None,
                "sa": agg.shots_against if agg else None,
                "fo_pct": agg.faceoff_pct if agg else None,
                "bs": agg.blocked_shots if agg else None,
                "hit": agg.hits if agg else None,
                "tka": agg.takeaways if agg else None,
                "gva": agg.giveaways if agg else None,
                "pp_ch": agg.pp_chances if agg else None,
                "ppg": agg.pp_goals if agg else None,
                "pp_pct": pp_pct,
                "pk_ga": agg.pk_goals_against if agg else None,
                "sh_ch": agg.sh_chances if agg else None,
                "pk_pct": pk_pct,
                "shg": agg.sh_goals if agg else None,
                "pim_g": agg.pim_per_game if agg else None,
                "att_h": avg_att_h,
                "cap_pct": cap_pct,
                "cf_pct": proc.get("cf_pct"),
                "ff_pct": proc.get("ff_pct"),
                "gf_per_60": proc.get("gf_per_60"),
                "ga_per_60": proc.get("ga_per_60"),
                "sq_hd": sq.get("high_danger_share"),
                "ranks": ranks,
                "gf_g": round(gf / gp, 2) if gf is not None and gp and gp > 0 else None,
                "ga_g": round(ga / gp, 2) if ga is not None and gp and gp > 0 else None,
                "shot_diff": shot_diff,
                "strength": strength,
                "situation_limited": strength != "all",
            }
        )

    card_rows: list[dict[str, Any]] = []
    for row in table_rows:
        card_rows.append(
            {
                "team": row["team"],
                "team_id": row["team_id"],
                "slug": row["slug"],
                "logo_url": row["logo_url"],
                "name": row["team"].full_display_name(),
                "abbr": row["team"].abbreviation or row["team"].name,
                "ranks": {
                    "point_pct": _rank_for_values(table_rows, "point_pct", row.get("point_pct"), True),
                    "gf_g": _rank_for_values(table_rows, "gf_g", row.get("gf_g"), True),
                    "ga_g": _rank_for_values(table_rows, "ga_g", row.get("ga_g"), False),
                    "pp_pct": row.get("ranks", {}).get("pp_pct"),
                    "pk_pct": row.get("ranks", {}).get("pk_pct"),
                    "shot_diff": _rank_for_values(table_rows, "shot_diff", row.get("shot_diff"), True),
                    "cf_pct": _rank_for_values(table_rows, "cf_pct", row.get("cf_pct"), True),
                    "sq_hd": _rank_for_values(table_rows, "sq_hd", row.get("sq_hd"), True),
                },
            }
        )

    seasons = _seasons_with_team_data(session)
    team_options = [
        {"slug": t.slug, "name": t.full_display_name(), "abbr": t.abbreviation or t.name}
        for t in sorted({r["team"] for r in table_rows}, key=lambda x: (x.name or "").lower())
    ]

    visible_columns = [c for c in TABLE_COLUMNS if strength in c.get("situations", ["all"])]

    return {
        "table_rows": table_rows,
        "card_rows": card_rows,
        "team_count": len(table_rows),
        "seasons": [{"id": int(s.id), "label": season_display_label(s)} for s in seasons],
        "team_options": team_options,
        "strength_options": STRENGTH_OPTIONS,
        "segments": TEAM_CHART_SEGMENTS,
        "columns": visible_columns,
        "card_metrics": CARD_METRICS,
        "rate": rate,
        "situation_note": (
            "Showing event-derived goals for the selected game strength. "
            "Hits, faceoffs, attendance, and other team_stats.csv columns apply to all situations only."
            if strength != "all"
            else None
        ),
    }
