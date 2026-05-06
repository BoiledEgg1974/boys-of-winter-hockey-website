"""Team-records page services: season summaries, season detail, leaderboards.

Reads team-by-team season rows from :class:`TeamSeasonRecord` (imported from
``team_season_records_template.csv``) and combines them with existing
``HistoryAward`` rows + per-season skater/goalie career lines for award &
top-10 rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    HistoryAward,
    Player,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Season,
    Team,
    TeamSeasonRecord,
)
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.division_labels import load_division_display_maps
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

TOP_N = 10
_MIN_GP_GOALIE_RATE_RS = 20
# NHL-era team-records leaderboards (Historical + Cap): only seasons with enough GP qualify.
_MIN_GP_TEAM_SEASON_LEADERBOARD = 30

CHAMPION_RESULT = "BOWL CUP CHAMPION"
RUNNER_UP_RESULT = "Lost Cup Finals"


# --------------------------- helpers --------------------------- #


def _is_null_sentinel(rec: TeamSeasonRecord, attr: str) -> bool:
    cols = (rec.null_columns_csv or "").split(",")
    return attr in cols


def render_record_value(
    rec: TeamSeasonRecord,
    attr: str,
    *,
    fmt: Callable[[Any], str] | None = None,
) -> str:
    """Render a numeric/text cell honouring the ``"null"`` sentinel rule.

    - field is ``None`` because the CSV cell was the literal ``"null"`` -> ``"-"``
    - field is ``None`` because the CSV cell was blank -> empty string
    - otherwise -> formatted value (``fmt`` if given, else ``str``)
    """
    val = getattr(rec, attr, None)
    if val is None:
        return "-" if _is_null_sentinel(rec, attr) else ""
    if fmt is not None:
        return fmt(val)
    return str(val)


def _label_start_year(label: str | None) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d{4})", str(label))
    return int(m.group(1)) if m else None


def team_display_name(rec: TeamSeasonRecord) -> str:
    if rec.team is not None:
        return rec.team.full_display_name()
    return (rec.team_name_override or "—").strip()


def team_season_logo_static_rel(rec: TeamSeasonRecord) -> str | None:
    """Return ``static/...`` relative path for a season-row logo override, if any."""
    raw = (rec.logo_file_override or "").strip()
    if not raw:
        return None
    raw = raw.lstrip("/\\").replace("\\", "/")
    if raw.startswith("static/"):
        raw = raw[len("static/"):]
    return raw


def standings_sort_key(rec: TeamSeasonRecord) -> tuple[float, float, float, float]:
    """Sort by PTS desc, then W desc, then Goal Diff desc, then GF desc."""
    pts = rec.pts if rec.pts is not None else -1
    w = rec.w if rec.w is not None else -1
    gd = rec.goal_diff if rec.goal_diff is not None else -10**9
    gf = rec.gf if rec.gf is not None else -1
    return (-float(pts), -float(w), -float(gd), -float(gf))


# --------------------------- data loading --------------------------- #


def _load_records_for_year(session: Session, year_label: str) -> list[TeamSeasonRecord]:
    rows = session.scalars(
        select(TeamSeasonRecord).where(TeamSeasonRecord.season_year_label == year_label)
    ).all()
    return sorted(rows, key=standings_sort_key)


def _load_all_records(session: Session) -> list[TeamSeasonRecord]:
    return list(session.scalars(select(TeamSeasonRecord)).all())


def all_year_labels_desc(session: Session) -> list[str]:
    rows = session.execute(
        select(TeamSeasonRecord.season_year_label, TeamSeasonRecord.start_year).distinct()
    ).all()
    seen: dict[str, int | None] = {}
    for yl, sy in rows:
        if yl in seen:
            continue
        seen[yl] = sy if sy is not None else _label_start_year(yl)
    return sorted(
        seen.keys(),
        key=lambda yl: (seen.get(yl) if seen.get(yl) is not None else _label_start_year(yl) or 0),
        reverse=True,
    )


def adjacent_years(session: Session, year_label: str) -> tuple[str | None, str | None]:
    labels = all_year_labels_desc(session)
    if year_label not in labels:
        return (None, None)
    i = labels.index(year_label)
    newer = labels[i - 1] if i > 0 else None
    older = labels[i + 1] if i + 1 < len(labels) else None
    return (newer, older)


# --------------------------- season summary cards --------------------------- #


@dataclass(frozen=True)
class SeasonSummaryCard:
    year_label: str
    start_year: int | None
    champion: TeamSeasonRecord | None
    runner_up: TeamSeasonRecord | None
    points_leader: TeamSeasonRecord | None
    top_scorer: dict[str, Any] | None
    top_goalie: dict[str, Any] | None


def _season_top_skater_for_year(
    session: Session, start_year: int | None
) -> dict[str, Any] | None:
    """Find the per-season top scorer using career lines (RS) for that ``start_year``."""
    if start_year is None:
        return None
    league_ids = bowl_nhl_league_ids(session) or (0,)
    lines = session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.season_year == int(start_year),
            PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
            PlayerSkaterCareerLine.career_source.in_(("rs", "retired_rs")),
        )
    ).all()
    if not lines:
        return None
    best_per_player: dict[int, PlayerSkaterCareerLine] = {}
    for ln in lines:
        cur = best_per_player.get(int(ln.player_id))
        if cur is None or int(ln.gp) > int(cur.gp):
            best_per_player[int(ln.player_id)] = ln

    def _pts(ln: PlayerSkaterCareerLine) -> int:
        return int(ln.goals or 0) + int(ln.assists or 0)

    chosen = max(best_per_player.values(), key=lambda ln: (_pts(ln), int(ln.goals or 0)), default=None)
    if chosen is None:
        return None
    pl = session.get(Player, chosen.player_id)
    if pl is None:
        return None
    team = session.get(Team, chosen.team_id) if chosen.team_id is not None else None
    return {
        "player": pl,
        "team": team,
        "goals": int(chosen.goals or 0),
        "assists": int(chosen.assists or 0),
        "points": _pts(chosen),
    }


def _season_top_goalie_for_year(
    session: Session, start_year: int | None
) -> dict[str, Any] | None:
    if start_year is None:
        return None
    league_ids = bowl_nhl_league_ids(session) or (0,)
    lines = session.scalars(
        select(PlayerGoalieCareerLine).where(
            PlayerGoalieCareerLine.season_year == int(start_year),
            PlayerGoalieCareerLine.league_fhm_id.in_(league_ids),
            PlayerGoalieCareerLine.career_source.in_(("rs", "retired_rs")),
        )
    ).all()
    if not lines:
        return None
    best_per_player: dict[int, PlayerGoalieCareerLine] = {}
    for ln in lines:
        cur = best_per_player.get(int(ln.player_id))
        if cur is None or int(ln.gp or 0) > int(cur.gp or 0):
            best_per_player[int(ln.player_id)] = ln
    chosen = max(
        best_per_player.values(), key=lambda ln: (int(ln.wins or 0), int(ln.gp or 0)), default=None
    )
    if chosen is None:
        return None
    pl = session.get(Player, chosen.player_id)
    if pl is None:
        return None
    team = session.get(Team, chosen.team_id) if chosen.team_id is not None else None
    return {
        "player": pl,
        "team": team,
        "wins": int(chosen.wins or 0),
        "losses": int(chosen.losses or 0),
        "shutouts": int(chosen.shutouts or 0),
    }


def list_season_summaries(session: Session) -> list[SeasonSummaryCard]:
    by_year: dict[str, list[TeamSeasonRecord]] = {}
    for r in _load_all_records(session):
        by_year.setdefault(r.season_year_label, []).append(r)
    cards: list[SeasonSummaryCard] = []
    for yl, recs in by_year.items():
        recs_sorted = sorted(recs, key=standings_sort_key)
        sy = next((r.start_year for r in recs if r.start_year is not None), _label_start_year(yl))
        champ = next((r for r in recs if (r.result or "") == CHAMPION_RESULT), None)
        runner = next((r for r in recs if (r.result or "") == RUNNER_UP_RESULT), None)
        pts_leader = recs_sorted[0] if recs_sorted else None
        cards.append(
            SeasonSummaryCard(
                year_label=yl,
                start_year=sy,
                champion=champ,
                runner_up=runner,
                points_leader=pts_leader,
                top_scorer=_season_top_skater_for_year(session, sy),
                top_goalie=_season_top_goalie_for_year(session, sy),
            )
        )
    cards.sort(key=lambda c: (c.start_year if c.start_year is not None else 0), reverse=True)
    return cards


# --------------------------- detail page builders --------------------------- #


def _load_conference_display_map(raw_dir: Path | None) -> dict[int, str]:
    out: dict[int, str] = {}
    if raw_dir is None:
        return out
    conf_csv = Path(raw_dir) / "conferences.csv"
    if not conf_csv.is_file():
        return out
    try:
        df = read_csv_normalized(conf_csv)
    except Exception:
        return out
    for _, row in df.iterrows():
        r = row.to_dict()
        rid = (cell_val(r, "conference id", "conference_id") or "").strip()
        nm = (cell_val(r, "name") or "").strip()
        if not rid or not nm:
            continue
        try:
            out[int(rid)] = nm
        except ValueError:
            continue
    return out


def _group_standings(
    records: list[TeamSeasonRecord],
    *,
    raw_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Group records into divisions (or a single 'League' bucket if no division info).

    Buckets are ordered by their highest PTS row.
    """
    buckets: dict[tuple[int | None, str, int | None, str], list[TeamSeasonRecord]] = {}
    conf_name_by_id = _load_conference_display_map(raw_dir)
    div_name_by_pair: dict[tuple[int, int], str] = {}
    div_name_by_id: dict[int, str] = {}
    if raw_dir is not None:
        div_name_by_pair, div_name_by_id = load_division_display_maps(Path(raw_dir) / "divisions.csv")

    def _key(r: TeamSeasonRecord) -> tuple[int | None, str, int | None, str]:
        conf = (r.conference_override or "").strip()
        div = (r.division_override or "").strip()
        if not conf and r.conference_id is not None:
            conf = conf_name_by_id.get(int(r.conference_id), f"Conf {r.conference_id}")
        if not div and r.division_id is not None:
            if r.conference_id is not None and (int(r.conference_id), int(r.division_id)) in div_name_by_pair:
                div = div_name_by_pair[(int(r.conference_id), int(r.division_id))]
            else:
                div = div_name_by_id.get(int(r.division_id), f"Div {r.division_id}")
        conf_id = int(r.conference_id) if r.conference_id is not None else None
        div_id = int(r.division_id) if r.division_id is not None else None
        return (conf_id, conf, div_id, div)

    for r in records:
        buckets.setdefault(_key(r), []).append(r)

    grouped: list[dict[str, Any]] = []
    for (conf_id, conf, div_id, div), rows in buckets.items():
        rows_sorted = sorted(rows, key=standings_sort_key)
        title_parts: list[str] = []
        if conf:
            title_parts.append(conf)
        if div and div != conf:
            title_parts.append(div)
        title = " — ".join(title_parts) if title_parts else "League"
        grouped.append(
            {
                "title": title,
                "rows": rows_sorted,
                "_conf_id": conf_id,
                "_div_id": div_id,
                "_conf_name": conf,
                "_div_name": div,
            }
        )

    grouped.sort(
        key=lambda b: (
            0 if b["_conf_id"] is not None else 1,
            b["_conf_id"] if b["_conf_id"] is not None else 10**9,
            (b["_conf_name"] or "").lower(),
            0 if b["_div_id"] is not None else 1,
            b["_div_id"] if b["_div_id"] is not None else 10**9,
            (b["_div_name"] or "").lower(),
        )
    )
    for b in grouped:
        b.pop("_conf_id", None)
        b.pop("_div_id", None)
        b.pop("_conf_name", None)
        b.pop("_div_name", None)
    return grouped


def _season_for_year(session: Session, year_label: str, start_year: int | None) -> Season | None:
    """Best-effort: locate a ``Season`` row matching the year for awards lookup."""
    candidates: list[Season] = list(
        session.scalars(select(Season).where(Season.label == year_label)).all()
    )
    if candidates:
        return candidates[0]
    if start_year is not None:
        candidates = list(
            session.scalars(select(Season).where(Season.start_year == int(start_year))).all()
        )
        # prefer the narrowest span if multiple exist
        narrow = [s for s in candidates if s.end_year is None or (int(s.end_year) - int(s.start_year) <= 2)]
        if narrow:
            return narrow[0]
        if candidates:
            return candidates[0]
    return None


def _awards_for_year(session: Session, year_label: str, start_year: int | None) -> list[HistoryAward]:
    return _awards_for_year_with_raw(session, year_label, start_year, raw_dir=None)


def raw_history_award_csv_season_labels(raw_dir: Path) -> set[str] | None:
    """Distinct ``season`` / ``season_id`` values present in the league awards CSV, if any.

    Resolution order matches :func:`scripts.import_pipeline.runner._history_awards_csv_path`.
    """
    for name in ("history_awards.sheet.csv", "history_awards.csv", "awards_history.csv"):
        p = raw_dir / name
        if not p.is_file():
            continue
        try:
            df = read_csv_normalized(p)
            out: set[str] = set()
            for _, row in df.iterrows():
                r = row.to_dict()
                s = (cell_val(r, "season_id", "season") or "").strip()
                if s:
                    out.add(s)
            return out
        except Exception:
            continue
    return None


def _awards_for_year_with_raw(
    session: Session,
    year_label: str,
    start_year: int | None,
    *,
    raw_dir: Path | None,
) -> list[Any]:
    """Awards for season detail.

    When a league awards CSV exists (sheet or narrow), use it as the only source for
    that season — including an empty list when the sheet has no rows for ``year_label``
    (do not fall back to DB, which may hold stale seasons not yet re-imported).
    """
    if raw_dir is not None:
        raw_csv: Path | None = None
        for name in ("history_awards.sheet.csv", "history_awards.csv", "awards_history.csv"):
            p = Path(raw_dir) / name
            if p.is_file():
                raw_csv = p
                break
        if raw_csv is not None:
            try:
                df = read_csv_normalized(raw_csv)
                out: list[Any] = []
                for _, row in df.iterrows():
                    r = row.to_dict()
                    season_cell = (cell_val(r, "season_id", "season") or "").strip()
                    if season_cell != year_label:
                        continue
                    player_key = (cell_val(r, "player_id", "fhm_player_id", "playerid") or "").strip()
                    team_key = (cell_val(r, "team_id", "team_abbr") or "").strip()
                    player = None
                    team = None
                    if player_key and player_key.lower() not in ("null", "none", "nan"):
                        player = session.scalars(
                            select(Player).where(Player.fhm_player_id == player_key).limit(1)
                        ).first()
                    if team_key:
                        team = session.scalars(
                            select(Team).where(Team.fhm_team_id == team_key).limit(1)
                        ).first()
                    out.append(
                        SimpleNamespace(
                            award_name=(cell_val(r, "award_name", "award") or "Award"),
                            player=player,
                            team=team,
                            notes=cell_val(r, "notes"),
                            staff_fhm_id=(cell_val(r, "staff_id", "staff_fhm_id", "fhm_staff_id") or "").strip() or None,
                            season=None,
                            season_id=None,
                        )
                    )
                out.sort(key=lambda a: (a.award_name or "").upper())
                return out
            except Exception:
                # Fall back to DB-backed awards.
                pass
    season = _season_for_year(session, year_label, start_year)
    if season is None:
        return []
    return list(
        session.scalars(
            select(HistoryAward)
            .where(HistoryAward.season_id == season.id)
            .order_by(HistoryAward.award_name.asc())
        ).all()
    )


# --------------------------- top-10 player builders for a single season --------------------------- #


def _skater_namespace_from_career(ln: PlayerSkaterCareerLine) -> Any:
    pts = int(ln.goals or 0) + int(ln.assists or 0)
    return SimpleNamespace(
        season_year=int(ln.season_year),
        team_fhm_id=int(ln.team_fhm_id or 0) if ln.team_fhm_id is not None else None,
        gp=int(ln.gp or 0),
        goals=int(ln.goals or 0),
        assists=int(ln.assists or 0),
        points=pts,
        pim=int(ln.pim or 0),
        plus_minus=ln.plus_minus,
        shots=ln.shots,
        ppg=ln.pp_goals,
        pp_assists=ln.pp_assists,
        shg=ln.sh_goals,
        sh_assists=ln.sh_assists,
        gwg=ln.gwg,
        hits=ln.hits,
    )


def _goalie_namespace_from_career(ln: PlayerGoalieCareerLine) -> Any:
    ga = int(ln.goals_against or 0)
    sa = int(ln.shots_against or 0)
    gp = int(ln.gp or 0)
    gaa = (float(ga) / float(gp)) if gp > 0 else None
    sv_pct = (float(sa - ga) / float(sa)) if sa > 0 else None
    return SimpleNamespace(
        season_year=int(ln.season_year),
        team_fhm_id=int(ln.team_fhm_id or 0) if ln.team_fhm_id is not None else None,
        gp=gp,
        wins=int(ln.wins or 0),
        losses=int(ln.losses or 0),
        ga=ga,
        sa=sa,
        so=int(ln.shutouts or 0),
        gaa=gaa,
        sv_pct=sv_pct,
    )


def _load_skater_rows_for_year(
    session: Session, start_year: int | None
) -> list[tuple[Any, Player, Team | None]]:
    if start_year is None:
        return []
    league_ids = bowl_nhl_league_ids(session) or (0,)
    out: list[tuple[Any, Player, Team | None]] = []
    seen_pids: set[int] = set()

    def _team_from_career_line(ln: PlayerSkaterCareerLine) -> Team | None:
        tm = session.get(Team, ln.team_id) if ln.team_id is not None else None
        if tm is None and getattr(ln, "team_fhm_id", None) is not None:
            tm = session.scalars(
                select(Team).where(Team.fhm_team_id == str(ln.team_fhm_id)).limit(1)
            ).first()
        return tm

    # A player can have multiple season rows (trades). Prefer rows with resolvable team;
    # otherwise highest GP row so Top-10 sections still pick a stable representative team.
    best_ln_by_pid: dict[int, tuple[PlayerSkaterCareerLine, Team | None]] = {}

    for ln in session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.season_year == int(start_year),
            PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
            PlayerSkaterCareerLine.career_source.in_(("rs", "retired_rs")),
        )
    ).all():
        pid = int(ln.player_id)
        tm = _team_from_career_line(ln)
        cur = best_ln_by_pid.get(pid)
        if cur is None:
            best_ln_by_pid[pid] = (ln, tm)
            continue
        cur_ln, cur_tm = cur
        cur_has_team = cur_tm is not None
        new_has_team = tm is not None
        if new_has_team and not cur_has_team:
            best_ln_by_pid[pid] = (ln, tm)
            continue
        if new_has_team == cur_has_team and int(ln.gp or 0) > int(cur_ln.gp or 0):
            best_ln_by_pid[pid] = (ln, tm)

    for pid, (ln, tm) in best_ln_by_pid.items():
        seen_pids.add(pid)
        pl = session.get(Player, ln.player_id)
        if pl is None:
            continue
        out.append((_skater_namespace_from_career(ln), pl, tm))

    season = session.scalars(select(Season).where(Season.start_year == int(start_year))).first()
    if season is not None:
        for st, pl, tm in session.execute(
            select(PlayerSkaterStat, Player, Team)
            .join(Player, Player.id == PlayerSkaterStat.player_id)
            .outerjoin(Team, Team.id == PlayerSkaterStat.team_id)
            .where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == "rs",
            )
        ).all():
            if int(st.player_id) in seen_pids:
                continue
            seen_pids.add(int(st.player_id))
            out.append((st, pl, tm))
    return out


def _load_goalie_rows_for_year(
    session: Session, start_year: int | None
) -> list[tuple[Any, Player, Team | None]]:
    if start_year is None:
        return []
    league_ids = bowl_nhl_league_ids(session) or (0,)
    out: list[tuple[Any, Player, Team | None]] = []
    seen_pids: set[int] = set()

    def _team_from_career_line(ln: PlayerGoalieCareerLine) -> Team | None:
        tm = session.get(Team, ln.team_id) if ln.team_id is not None else None
        if tm is None and getattr(ln, "team_fhm_id", None) is not None:
            tm = session.scalars(
                select(Team).where(Team.fhm_team_id == str(ln.team_fhm_id)).limit(1)
            ).first()
        return tm

    best_ln_by_pid: dict[int, tuple[PlayerGoalieCareerLine, Team | None]] = {}
    for ln in session.scalars(
        select(PlayerGoalieCareerLine).where(
            PlayerGoalieCareerLine.season_year == int(start_year),
            PlayerGoalieCareerLine.league_fhm_id.in_(league_ids),
            PlayerGoalieCareerLine.career_source.in_(("rs", "retired_rs")),
        )
    ).all():
        pid = int(ln.player_id)
        tm = _team_from_career_line(ln)
        cur = best_ln_by_pid.get(pid)
        if cur is None:
            best_ln_by_pid[pid] = (ln, tm)
            continue
        cur_ln, cur_tm = cur
        cur_has_team = cur_tm is not None
        new_has_team = tm is not None
        if new_has_team and not cur_has_team:
            best_ln_by_pid[pid] = (ln, tm)
            continue
        if new_has_team == cur_has_team and int(ln.gp or 0) > int(cur_ln.gp or 0):
            best_ln_by_pid[pid] = (ln, tm)

    for pid, (ln, tm) in best_ln_by_pid.items():
        seen_pids.add(pid)
        pl = session.get(Player, ln.player_id)
        if pl is None:
            continue
        out.append((_goalie_namespace_from_career(ln), pl, tm))
    season = session.scalars(select(Season).where(Season.start_year == int(start_year))).first()
    if season is not None:
        for st, pl, tm in session.execute(
            select(PlayerGoalieStat, Player, Team)
            .join(Player, Player.id == PlayerGoalieStat.player_id)
            .outerjoin(Team, Team.id == PlayerGoalieStat.team_id)
            .where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.stat_segment == "rs",
            )
        ).all():
            if int(st.player_id) in seen_pids:
                continue
            seen_pids.add(int(st.player_id))
            out.append((st, pl, tm))
    return out


def _top_skater_rows(
    rows: Iterable[tuple[Any, Player, Team | None]],
    *,
    title: str,
    value_fn: Callable[[Any], int | float | None],
    maximize: bool = True,
    fmt: Callable[[float | int], str] | None = None,
    row_filter: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    scored: list[tuple[float, Any, Player, Team | None, float | int]] = []
    for st, pl, tm in rows:
        if row_filter is not None and not row_filter(st):
            continue
        v = value_fn(st)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv != fv:
            continue
        scored.append((fv, st, pl, tm, v))
    scored.sort(key=lambda x: (-x[0], (x[2].last_name or "").lower()) if maximize else (x[0], (x[2].last_name or "").lower()))
    out: list[dict[str, Any]] = []
    for i, (_, st_row, pl, tm, raw) in enumerate(scored[:TOP_N], start=1):
        out.append(
            {
                "rank": i,
                "player": pl,
                "team": tm,
                "logo_record": st_row,
                "value": fmt(raw) if fmt is not None else (str(int(raw)) if isinstance(raw, (int, float)) and float(raw) == int(raw) else str(raw)),
            }
        )
    return {"title": title, "rows": out}


def season_top10_skaters(session: Session, start_year: int | None) -> list[dict[str, Any]]:
    rows = _load_skater_rows_for_year(session, start_year)
    return [
        _top_skater_rows(rows, title="Points", value_fn=lambda s: getattr(s, "points", None)),
        _top_skater_rows(rows, title="Goals", value_fn=lambda s: getattr(s, "goals", None)),
        _top_skater_rows(rows, title="Assists", value_fn=lambda s: getattr(s, "assists", None)),
        _top_skater_rows(rows, title="PIM", value_fn=lambda s: getattr(s, "pim", None)),
    ]


def season_top10_goalies(session: Session, start_year: int | None) -> list[dict[str, Any]]:
    rows = _load_goalie_rows_for_year(session, start_year)
    sections: list[dict[str, Any]] = []
    sections.append(_top_skater_rows(rows, title="Wins", value_fn=lambda s: getattr(s, "wins", None)))
    sections.append(_top_skater_rows(rows, title="Shutouts", value_fn=lambda s: getattr(s, "so", None)))

    def _gaa(s: Any) -> float | None:
        v = getattr(s, "gaa", None)
        return float(v) if v is not None else None

    sections.append(
        _top_skater_rows(
            rows,
            title=f"GAA (Min. {_MIN_GP_GOALIE_RATE_RS} GP)",
            value_fn=_gaa,
            maximize=False,
            row_filter=lambda s: int(getattr(s, "gp", 0) or 0) >= _MIN_GP_GOALIE_RATE_RS,
            fmt=lambda v: f"{float(v):.2f}",
        )
    )

    def _sv(s: Any) -> float | None:
        v = getattr(s, "sv_pct", None)
        return float(v) if v is not None else None

    sections.append(
        _top_skater_rows(
            rows,
            title=f"Save % (Min. {_MIN_GP_GOALIE_RATE_RS} GP)",
            value_fn=_sv,
            row_filter=lambda s: int(getattr(s, "gp", 0) or 0) >= _MIN_GP_GOALIE_RATE_RS,
            fmt=lambda v: (f"{float(v):.3f}").lstrip("0") or "0.000",
        )
    )
    return sections


# --------------------------- season detail bundle --------------------------- #


@dataclass
class SeasonDetail:
    year_label: str
    start_year: int | None
    standings_groups: list[dict[str, Any]]
    skater_sections: list[dict[str, Any]]
    goalie_sections: list[dict[str, Any]]
    awards: list[HistoryAward]


def season_detail(session: Session, year_label: str, *, raw_dir: Path | None = None) -> SeasonDetail | None:
    records = _load_records_for_year(session, year_label)
    if not records:
        return None
    sy = next((r.start_year for r in records if r.start_year is not None), _label_start_year(year_label))
    return SeasonDetail(
        year_label=year_label,
        start_year=sy,
        standings_groups=_group_standings(records, raw_dir=raw_dir),
        skater_sections=season_top10_skaters(session, sy),
        goalie_sections=season_top10_goalies(session, sy),
        awards=_awards_for_year_with_raw(session, year_label, sy, raw_dir=raw_dir),
    )


# --------------------------- team-level helpers --------------------------- #


def team_year_records(session: Session, team: Team) -> list[TeamSeasonRecord]:
    rows = session.scalars(
        select(TeamSeasonRecord).where(TeamSeasonRecord.team_id == team.id)
    ).all()
    return sorted(
        rows,
        key=lambda r: (r.start_year if r.start_year is not None else _label_start_year(r.season_year_label) or 0),
    )


# --------------------------- team-season leaderboards --------------------------- #


@dataclass(frozen=True)
class LeaderboardSection:
    title: str
    rows: list[dict[str, Any]]


_FORMAT_PCT = lambda v: f"{float(v):.1f}"  # noqa: E731
_FORMAT_RATE = lambda v: f"{float(v):.2f}"  # noqa: E731
_FORMAT_INT = lambda v: str(int(round(float(v))))  # noqa: E731


def _leaderboard_min_gp_enabled_for_slug(league_slug: str | None) -> bool:
    s = (league_slug or "").strip()
    return s in frozenset({"bowl-historical", "bowl-cap"})


def _leaderboard_resolve_league_slug(explicit: str | None) -> str | None:
    s = (explicit or "").strip()
    if s:
        return s
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            c = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
            return c or None
    except Exception:
        pass
    return None


def _leaderboard_gp_qualifies(r: TeamSeasonRecord) -> bool:
    if _is_null_sentinel(r, "gp"):
        return False
    g = r.gp
    if g is None:
        return False
    try:
        return int(g) >= _MIN_GP_TEAM_SEASON_LEADERBOARD
    except (TypeError, ValueError):
        return False


def _leaderboard_rows(
    records: Iterable[TeamSeasonRecord],
    *,
    attr: str,
    maximize: bool,
    fmt: Callable[[Any], str] = _FORMAT_INT,
    extra_filter: Callable[[TeamSeasonRecord], bool] | None = None,
    use_min_gp: bool = False,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, TeamSeasonRecord, Any]] = []
    for r in records:
        if use_min_gp and not _leaderboard_gp_qualifies(r):
            continue
        if _is_null_sentinel(r, attr):
            continue
        v = getattr(r, attr, None)
        if v is None:
            continue
        if extra_filter is not None and not extra_filter(r):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv != fv:
            continue
        tname = team_display_name(r).lower()
        scored.append((fv, tname, r, v))
    if maximize:
        scored.sort(key=lambda t: (-t[0], t[1], t[2].season_year_label))
    else:
        scored.sort(key=lambda t: (t[0], t[1], t[2].season_year_label))
    out: list[dict[str, Any]] = []
    for i, (_, _, r, raw) in enumerate(scored[:TOP_N], start=1):
        out.append(
            {
                "rank": i,
                "record": r,
                "team": r.team,
                "team_name": team_display_name(r),
                "team_logo_override_rel": team_season_logo_static_rel(r),
                "year_label": r.season_year_label,
                "value": fmt(raw),
            }
        )
    return out


def _has_any(records: list[TeamSeasonRecord], attr: str) -> bool:
    return any(getattr(r, attr, None) is not None and not _is_null_sentinel(r, attr) for r in records)


def build_team_record_leaderboards(
    session: Session,
    *,
    league_slug: str | None = None,
) -> list[LeaderboardSection]:
    """The 30+ Most/Fewest panels listed in the original request."""
    records = _load_all_records(session)
    use_min_gp = _leaderboard_min_gp_enabled_for_slug(_leaderboard_resolve_league_slug(league_slug))

    sections: list[LeaderboardSection] = []

    def add(
        title: str,
        attr: str,
        *,
        maximize: bool,
        fmt: Callable[[Any], str] = _FORMAT_INT,
        extra_filter: Callable[[TeamSeasonRecord], bool] | None = None,
    ) -> None:
        if not _has_any(records, attr):
            return
        rows = _leaderboard_rows(
            records,
            attr=attr,
            maximize=maximize,
            fmt=fmt,
            extra_filter=extra_filter,
            use_min_gp=use_min_gp,
        )
        if rows:
            sections.append(LeaderboardSection(title=title, rows=rows))

    # ---- standings-style ----
    add("Most Points", "pts", maximize=True)
    add("Fewest Points", "pts", maximize=False)
    add("Most Wins", "w", maximize=True)
    add("Fewest Wins", "w", maximize=False)
    add("Most Losses", "l", maximize=True)
    add("Fewest Losses", "l", maximize=False)
    add("Most Ties/OTL", "t_otl", maximize=True)
    add("Fewest Ties/OTL", "t_otl", maximize=False)
    add("Highest Goal Differential", "goal_diff", maximize=True)
    add("Lowest Goal Differential", "goal_diff", maximize=False)

    # ---- offence / defence ----
    add("Most Goals For", "gf", maximize=True)
    add("Fewest Goals For", "gf", maximize=False)
    add("Most Goals Against", "ga", maximize=True)
    add("Fewest Goals Against", "ga", maximize=False)
    add("Most Shots For", "shots_for", maximize=True)
    add("Fewest Shots For", "shots_for", maximize=False)
    add("Most Shots Against", "shots_against", maximize=True)
    add("Fewest Shots Against", "shots_against", maximize=False)

    # ---- special teams ----
    add("Highest PP%", "pp_pct", maximize=True, fmt=_FORMAT_PCT)
    add("Lowest PP%", "pp_pct", maximize=False, fmt=_FORMAT_PCT)
    add("Highest PK%", "pk_pct", maximize=True, fmt=_FORMAT_PCT)
    add("Lowest PK%", "pk_pct", maximize=False, fmt=_FORMAT_PCT)
    add("Most PPG For", "ppg", maximize=True)
    add("Fewest PPG For", "ppg", maximize=False)
    add("Most PPG Against", "ppg_against", maximize=True)
    add("Fewest PPG Against", "ppg_against", maximize=False)
    add("Most PP Chances", "pp_chances", maximize=True)
    add("Fewest PP Chances", "pp_chances", maximize=False)
    add("Most SH Chances", "sh_chances", maximize=True)
    add("Fewest SH Chances", "sh_chances", maximize=False)
    add("Most SHG For", "shg", maximize=True)
    add("Fewest SHG For", "shg", maximize=False)
    add("Most SHG Against", "shg_against", maximize=True)
    add("Fewest SHG Against", "shg_against", maximize=False)

    # ---- discipline ----
    add("Highest PIM/G", "pim_per_game", maximize=True, fmt=_FORMAT_RATE)
    add("Lowest PIM/G", "pim_per_game", maximize=False, fmt=_FORMAT_RATE)

    return sections
