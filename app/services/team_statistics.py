"""Team Statistics page — league-wide team stats, filters, and chart archive."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from types import SimpleNamespace
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
    TeamSeasonRecord,
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


def rank_maps_for_segment(
    session: Session,
    season_id: int,
    segment: str,
    *,
    team_ids: frozenset[int] | None = None,
) -> dict[int, dict[str, int]]:
    """League-wide dense ranks per stat key for TeamSeasonAggregate rows in one segment."""
    aggs = session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season_id,
            TeamSeasonAggregate.stat_segment == segment,
        )
    ).all()
    if team_ids is not None:
        aggs = [a for a in aggs if int(a.team_id) in team_ids]
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


_MIN_CATALOG_YEAR = 1900


def _hockey_year_label(start_year: int) -> str:
    y = int(start_year)
    return f"{y}-{(y + 1) % 100:02d}"


def _start_year_from_label(label: str | None) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d{4})", str(label))
    return int(m.group(1)) if m else None


def _as_pct(value: float | int | None) -> float | None:
    if value is None:
        return None
    num = float(value)
    if 0 < num <= 1.0:
        return round(num * 100.0, 1)
    return round(num, 1)


def season_ref_for_catalog_year(*, start_year: int, label: str) -> SimpleNamespace:
    """Season-like object so templates/logo helpers can render a catalog year."""
    return SimpleNamespace(
        id=None,
        start_year=int(start_year),
        end_year=int(start_year) + 1,
        label=label,
        is_current=False,
    )


def build_team_statistics_season_options(session: Session) -> list[dict[str, Any]]:
    """Catalog every team-stats year this league DB can show.

    FHM reuses a single live ``Season`` row, so the dropdown also includes
    rollover archives and ``TeamSeasonRecord`` history. Same helper is used by
    Historical, Cap, and Relegation — each mount reads its own database.
    """
    from app.services.analytics_snapshots import load_team_rollover_years
    from app.services.team_records import all_year_labels_desc

    by_year: dict[int, dict[str, Any]] = {}

    for label in all_year_labels_desc(session):
        sy = _start_year_from_label(label)
        if sy is None or sy < _MIN_CATALOG_YEAR:
            continue
        by_year[sy] = {
            "key": f"y:{sy}",
            "season_id": None,
            "start_year": sy,
            "label": str(label),
            "source": "records",
        }

    for year in load_team_rollover_years(session):
        sy = int(year)
        if sy < _MIN_CATALOG_YEAR:
            continue
        prev = by_year.get(sy) or {}
        by_year[sy] = {
            "key": f"y:{sy}",
            "season_id": None,
            "start_year": sy,
            "label": str(prev.get("label") or _hockey_year_label(sy)),
            "source": "archive",
        }

    for season in _seasons_with_team_data(session):
        label = season_display_label(season)
        if season.start_year is None:
            by_year[(-1000000) - int(season.id)] = {
                "key": f"s:{int(season.id)}",
                "season_id": int(season.id),
                "start_year": None,
                "label": label,
                "source": "live",
            }
            continue
        sy = int(season.start_year)
        by_year[sy] = {
            "key": f"s:{int(season.id)}",
            "season_id": int(season.id),
            "start_year": sy,
            "label": label,
            "source": "live",
        }

    options = [opt for opt in by_year.values() if opt.get("key")]
    options.sort(
        key=lambda s: (
            int(s["start_year"]) if s.get("start_year") is not None else -1,
            str(s["key"]),
        ),
        reverse=True,
    )
    return options


def resolve_team_statistics_season_option(
    options: list[dict[str, Any]],
    *,
    season_key: str | None = None,
    season_id: int | None = None,
    live_season: Season | None = None,
) -> dict[str, Any] | None:
    """Pick a catalog option from ``?season=`` / ``?season_id=`` / the live year."""
    key = (season_key or "").strip()
    if key:
        for opt in options:
            if str(opt.get("key")) == key:
                return opt
        if key.startswith("s:"):
            try:
                sid = int(key[2:])
            except ValueError:
                sid = None
            if sid is not None:
                for opt in options:
                    if opt.get("season_id") == sid:
                        return opt
        if key.startswith("y:"):
            try:
                year = int(key[2:])
            except ValueError:
                year = None
            if year is not None:
                for opt in options:
                    if opt.get("start_year") == year:
                        return opt
        label_year = _start_year_from_label(key)
        if label_year is not None:
            for opt in options:
                if opt.get("start_year") == label_year:
                    return opt
    if season_id is not None:
        sid = int(season_id)
        for opt in options:
            if opt.get("season_id") == sid:
                return opt
    if live_season is not None:
        for opt in options:
            if opt.get("season_id") == int(live_season.id):
                return opt
        return {
            "key": f"s:{int(live_season.id)}",
            "season_id": int(live_season.id),
            "start_year": live_season.start_year,
            "label": season_display_label(live_season),
            "source": "live",
        }
    return options[0] if options else None


def _snapshot_metrics_by_team(
    session: Session, *, season_year: int, segment: str
) -> dict[int, dict[str, Any]]:
    from app.services.analytics_snapshots import latest_team_rollover_rows

    out: dict[int, dict[str, Any]] = {}
    for snap in latest_team_rollover_rows(session, season_year=int(season_year), segment=segment):
        try:
            metrics = json.loads(snap.metrics_json or "{}")
        except json.JSONDecodeError:
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {}
        out[int(snap.team_id)] = {"team": snap.team, "metrics": metrics}
    return out


def _empty_stat_row(team: Team, *, display_name: str, logo_url: str | None) -> dict[str, Any]:
    return {
        "team": team,
        "team_id": int(team.id),
        "slug": team.slug,
        "display_name": display_name,
        "logo_url": logo_url,
        "primary_color": team.primary_color,
        "gp": None,
        "w": None,
        "l": None,
        "pts": None,
        "point_pct": None,
        "gf": None,
        "ga": None,
        "diff": None,
        "sf": None,
        "sa": None,
        "fo_pct": None,
        "bs": None,
        "hit": None,
        "tka": None,
        "gva": None,
        "pp_ch": None,
        "ppg": None,
        "pp_pct": None,
        "pk_ga": None,
        "sh_ch": None,
        "pk_pct": None,
        "shg": None,
        "pim_g": None,
        "att_h": None,
        "cap_pct": None,
        "cf_pct": None,
        "ff_pct": None,
        "gf_per_60": None,
        "ga_per_60": None,
        "sq_hd": None,
        "ranks": {},
        "gf_g": None,
        "ga_g": None,
        "shot_diff": None,
        "strength": "all",
        "situation_limited": False,
    }


def _apply_snapshot_metrics(row: dict[str, Any], metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not metrics:
        return row
    if row.get("gp") is None and metrics.get("gp") is not None:
        row["gp"] = int(metrics["gp"]) if metrics["gp"] is not None else None
    if row.get("gf") is None:
        row["gf"] = metrics.get("gf")
    if row.get("ga") is None:
        row["ga"] = metrics.get("ga")
    if row.get("diff") is None:
        row["diff"] = metrics.get("goal_diff")
    if row.get("sf") is None:
        row["sf"] = metrics.get("shots_for")
    if row.get("sa") is None:
        row["sa"] = metrics.get("shots_against")
    if row.get("shot_diff") is None:
        row["shot_diff"] = metrics.get("shot_diff")
    if row.get("pp_pct") is None:
        row["pp_pct"] = _as_pct(metrics.get("pp_pct"))
    if row.get("pk_pct") is None:
        row["pk_pct"] = _as_pct(metrics.get("pk_pct"))
    if row.get("pts") is None:
        row["pts"] = metrics.get("pts")
    if row.get("point_pct") is None:
        row["point_pct"] = metrics.get("point_pct")
    if row.get("sq_hd") is None:
        row["sq_hd"] = metrics.get("sq_high_danger")
    gp = row.get("gp")
    gf = row.get("gf")
    ga = row.get("ga")
    if row.get("gf_g") is None and gf is not None and gp:
        row["gf_g"] = round(float(gf) / float(gp), 2)
    if row.get("ga_g") is None and ga is not None and gp:
        row["ga_g"] = round(float(ga) / float(gp), 2)
    return row


def _table_row_from_record(
    rec: TeamSeasonRecord,
    *,
    team: Team,
    display_name: str,
    logo_url: str | None,
) -> dict[str, Any]:
    gp = int(rec.gp) if rec.gp is not None else None
    gf = int(rec.gf) if rec.gf is not None else None
    ga = int(rec.ga) if rec.ga is not None else None
    diff = rec.goal_diff
    if diff is None and gf is not None and ga is not None:
        diff = gf - ga
    pts = int(rec.pts) if rec.pts is not None else None
    pp_pct = rec.pp_pct
    if pp_pct is None:
        pp_pct = _pp_pct(rec.ppg, rec.pp_chances)
    else:
        pp_pct = _as_pct(pp_pct)
    pk_pct = rec.pk_pct
    if pk_pct is None:
        pk_pct = _pk_pct(rec.ppg_against, rec.sh_chances)
    else:
        pk_pct = _as_pct(pk_pct)
    point_pct = round(100.0 * pts / (gp * 2), 1) if pts is not None and gp and gp > 0 else None
    shot_diff = (
        (rec.shots_for - rec.shots_against)
        if rec.shots_for is not None and rec.shots_against is not None
        else None
    )
    row = _empty_stat_row(team, display_name=display_name, logo_url=logo_url)
    row.update(
        {
            "gp": gp,
            "w": int(rec.w) if rec.w is not None else None,
            "l": int(rec.l) if rec.l is not None else None,
            "pts": pts,
            "point_pct": point_pct,
            "gf": gf,
            "ga": ga,
            "diff": diff,
            "sf": rec.shots_for,
            "sa": rec.shots_against,
            "pp_ch": rec.pp_chances,
            "ppg": rec.ppg,
            "pp_pct": pp_pct,
            "pk_ga": rec.ppg_against,
            "sh_ch": rec.sh_chances,
            "pk_pct": pk_pct,
            "shg": rec.shg,
            "pim_g": rec.pim_per_game,
            "gf_g": round(gf / gp, 2) if gf is not None and gp and gp > 0 else None,
            "ga_g": round(ga / gp, 2) if ga is not None and gp and gp > 0 else None,
            "shot_diff": shot_diff,
        }
    )
    return row


def _card_rows_from_table(table_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    card_rows: list[dict[str, Any]] = []
    for row in table_rows:
        card_rows.append(
            {
                "team": row["team"],
                "team_id": row["team_id"],
                "slug": row["slug"],
                "logo_url": row["logo_url"],
                "name": row.get("display_name") or row["team"].full_display_name(),
                "abbr": row["team"].abbreviation or row["team"].name,
                "ranks": {
                    "point_pct": _rank_for_values(table_rows, "point_pct", row.get("point_pct"), True),
                    "gf_g": _rank_for_values(table_rows, "gf_g", row.get("gf_g"), True),
                    "ga_g": _rank_for_values(table_rows, "ga_g", row.get("ga_g"), False),
                    "pp_pct": _rank_for_values(table_rows, "pp_pct", row.get("pp_pct"), True),
                    "pk_pct": _rank_for_values(table_rows, "pk_pct", row.get("pk_pct"), True),
                    "shot_diff": _rank_for_values(table_rows, "shot_diff", row.get("shot_diff"), True),
                    "cf_pct": _rank_for_values(table_rows, "cf_pct", row.get("cf_pct"), True),
                    "sq_hd": _rank_for_values(table_rows, "sq_hd", row.get("sq_hd"), True),
                },
            }
        )
    return card_rows


def _payload_from_catalog_year(
    session: Session,
    *,
    start_year: int,
    year_label: str,
    selected_season_key: str,
    segment: str,
    rate: str,
    team_slugs: list[str] | None,
    seasons: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.season_team_logo_bundle import get_season_team_logo_bundle
    from app.services.team_records import _load_records_for_year, team_display_name

    slug_filter: set[str] | None = None
    if team_slugs:
        slug_filter = {s.strip().lower() for s in team_slugs if s.strip()}

    logo_bundle = get_season_team_logo_bundle()
    recs = _load_records_for_year(session, year_label) if segment == "rs" else []
    snaps = _snapshot_metrics_by_team(session, season_year=start_year, segment=segment)

    table_rows: list[dict[str, Any]] = []
    seen_team_ids: set[int] = set()
    for rec in recs:
        team = rec.team
        if team is None and rec.team_id is not None:
            team = session.get(Team, int(rec.team_id))
        if team is None:
            continue
        if slug_filter and (team.slug or "").lower() not in slug_filter:
            continue
        display_name = team_display_name(rec, session=session)
        row = _table_row_from_record(
            rec,
            team=team,
            display_name=display_name,
            logo_url=logo_bundle.team_logo_url_for_season_context(team, start_year),
        )
        snap = snaps.get(int(team.id))
        if snap:
            _apply_snapshot_metrics(row, snap.get("metrics"))
        table_rows.append(row)
        seen_team_ids.add(int(team.id))

    for tid, snap in snaps.items():
        if tid in seen_team_ids:
            continue
        team = snap.get("team")
        if team is None:
            team = session.get(Team, tid)
        if team is None:
            continue
        if slug_filter and (team.slug or "").lower() not in slug_filter:
            continue
        row = _empty_stat_row(
            team,
            display_name=team.full_display_name(),
            logo_url=logo_bundle.team_logo_url_for_season_context(team, start_year),
        )
        _apply_snapshot_metrics(row, snap.get("metrics"))
        table_rows.append(row)

    team_options = [
        {"slug": t.slug, "name": t.full_display_name(), "abbr": t.abbreviation or t.name}
        for t in sorted({r["team"] for r in table_rows}, key=lambda x: (x.name or "").lower())
    ]
    note = None
    if segment != "rs" and not table_rows:
        note = "Playoff and preseason team totals are only available for imported seasons and archived analytics snapshots."
    elif segment != "rs":
        note = "Catalog years use archived analytics for playoffs and preseason when a snapshot exists."
    else:
        note = (
            "Showing the season catalog (team records and any archived analytics). "
            "Hits, faceoffs, CF%, and shot quality appear when that year was snapshotted on rollover."
        )

    return {
        "table_rows": table_rows,
        "card_rows": _card_rows_from_table(table_rows),
        "team_count": len(table_rows),
        "seasons": seasons,
        "selected_season_key": selected_season_key,
        "team_options": team_options,
        "strength_options": STRENGTH_OPTIONS,
        "segments": TEAM_CHART_SEGMENTS,
        "columns": [c for c in TABLE_COLUMNS if "all" in c.get("situations", ["all"])],
        "card_metrics": CARD_METRICS,
        "rate": rate,
        "situation_note": note,
        "catalog_year": True,
    }


def _append_record_years_to_chart_archive(session: Session, archive: dict[str, Any]) -> dict[str, Any]:
    """Add TeamSeasonRecord years to the chart so the Season dropdown matches the catalog."""
    from app.services.season_team_logo_bundle import get_season_team_logo_bundle
    from app.services.team_records import _load_all_records, _records_have_displayable_standings, team_display_name

    existing_years: set[int] = set()
    for opt in archive.get("seasons") or []:
        sy = opt.get("start_year")
        if sy is not None:
            existing_years.add(int(sy))

    grouped: dict[int, list[TeamSeasonRecord]] = defaultdict(list)
    labels: dict[int, str] = {}
    for rec in _load_all_records(session):
        sy = rec.start_year if rec.start_year is not None else _start_year_from_label(rec.season_year_label)
        if sy is None or int(sy) < _MIN_CATALOG_YEAR:
            continue
        year = int(sy)
        grouped[year].append(rec)
        labels.setdefault(year, rec.season_year_label)

    if not grouped:
        return archive

    logo_bundle = get_season_team_logo_bundle()
    datasets = archive.setdefault("datasets", {})
    season_options = list(archive.get("seasons") or [])
    added = False
    for year, recs in grouped.items():
        if year in existing_years:
            continue
        if not _records_have_displayable_standings(recs):
            continue
        payload_rows: list[dict[str, Any]] = []
        for rec in recs:
            team = rec.team
            if team is None and rec.team_id is not None:
                team = session.get(Team, int(rec.team_id))
            if team is None:
                continue
            gp = int(rec.gp or 0)
            gf = rec.gf
            ga = rec.ga
            pts = rec.pts
            pp_pct = rec.pp_pct if rec.pp_pct is not None else _pp_pct(rec.ppg, rec.pp_chances)
            pk_pct = rec.pk_pct if rec.pk_pct is not None else _pk_pct(rec.ppg_against, rec.sh_chances)
            metrics = {
                "gp": gp if gp > 0 else None,
                "gf": gf,
                "ga": ga,
                "goal_diff": rec.goal_diff if rec.goal_diff is not None else (
                    (int(gf) - int(ga)) if gf is not None and ga is not None else None
                ),
                "shots_for": rec.shots_for,
                "shots_against": rec.shots_against,
                "shot_diff": (
                    (rec.shots_for - rec.shots_against)
                    if rec.shots_for is not None and rec.shots_against is not None
                    else None
                ),
                "pp_pct": _as_pct(pp_pct) if pp_pct is not None else None,
                "pk_pct": _as_pct(pk_pct) if pk_pct is not None else None,
                "sq_high_danger": None,
                "pts": pts,
                "point_pct": round(100.0 * int(pts) / (gp * 2), 1) if pts is not None and gp > 0 else None,
                "points_above_ppg": (int(pts) - gp) if pts is not None and gp > 0 else None,
            }
            if not any(v is not None for k, v in metrics.items() if k != "gp"):
                continue
            payload_rows.append(
                {
                    "team_id": int(team.id),
                    "name": team_display_name(rec, session=session),
                    "abbr": (team.abbreviation or team.name or "").strip(),
                    "slug": team.slug,
                    "logo_url": logo_bundle.team_logo_url_for_season_context(team, year),
                    "primary_color": team.primary_color,
                    "metrics": metrics,
                    "season_label": labels.get(year) or _hockey_year_label(year),
                }
            )
        if not payload_rows:
            continue
        datasets[f"y:{year}|rs"] = {"teams": payload_rows}
        season_options.append(
            {
                "id": f"y:{year}",
                "label": labels.get(year) or _hockey_year_label(year),
                "start_year": year,
            }
        )
        existing_years.add(year)
        added = True

    if added:
        season_options.sort(
            key=lambda s: (
                int(s["start_year"]) if s.get("start_year") is not None else -1,
                str(s["id"]),
            ),
            reverse=True,
        )
        archive["seasons"] = season_options
    return archive


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
    scoped_team_ids: frozenset[int] | None = None,
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
        season_key = parts[0]
        if season_key.startswith("y:"):
            # Archived rollover snapshot — metrics already loaded from snapshot JSON.
            continue
        try:
            season_id = int(season_key)
        except ValueError:
            continue
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
    if scoped_team_ids is not None:
        for ds in archive.get("datasets", {}).values():
            ds["teams"] = [
                t for t in ds.get("teams", []) if int(t.get("team_id", -1)) in scoped_team_ids
            ]
    return _append_record_years_to_chart_archive(session, archive)


def build_team_statistics_page_payload(
    session: Session,
    *,
    season: Season,
    segment: str = "rs",
    strength: str = "all",
    rate: str = "raw",
    team_slugs: list[str] | None = None,
    standings_rows: list | None = None,
    selected_season_key: str | None = None,
    history_year: int | None = None,
    history_year_label: str | None = None,
    scoped_team_ids: frozenset[int] | None = None,
) -> dict[str, Any]:
    from app.services.season_team_logo_bundle import get_season_team_logo_bundle

    seasons = build_team_statistics_season_options(session)
    live_id = getattr(season, "id", None)
    live_key = f"s:{int(live_id)}" if live_id is not None else None
    if history_year is not None:
        label = history_year_label or _hockey_year_label(int(history_year))
        return _payload_from_catalog_year(
            session,
            start_year=int(history_year),
            year_label=label,
            selected_season_key=selected_season_key or f"y:{int(history_year)}",
            segment=segment,
            rate=rate,
            team_slugs=team_slugs,
            seasons=seasons,
        )

    if standings_rows is None:
        standings_rows = standings_for_season(season)

    stat_rows = team_aggregate_rows(season, standings_rows, segment)
    standings_by_team = {int(st.team_id): st for st in standings_rows}
    rank_maps = rank_maps_for_segment(
        session, int(season.id), segment, team_ids=scoped_team_ids
    )
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
                "display_name": team.full_display_name(),
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

    team_options = [
        {"slug": t.slug, "name": t.full_display_name(), "abbr": t.abbreviation or t.name}
        for t in sorted({r["team"] for r in table_rows}, key=lambda x: (x.name or "").lower())
    ]

    visible_columns = [c for c in TABLE_COLUMNS if strength in c.get("situations", ["all"])]

    return {
        "table_rows": table_rows,
        "card_rows": card_rows,
        "team_count": len(table_rows),
        "seasons": seasons,
        "selected_season_key": selected_season_key or live_key,
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
        "catalog_year": False,
    }
