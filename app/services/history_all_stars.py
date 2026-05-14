"""League History: First / Second all-star team tables from ``history_all_stars`` rows."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAllStar,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Season,
    Team,
)

_SEASON_TOKEN = re.compile(r"\b(\d{4}-\d{2})\b")
_LABEL_START_YEAR = re.compile(r"^(\d{4})-\d{2}$")


def _normalize_all_star_sheet_label(s: str) -> str:
    """Normalize sheet / UI keys so ``1990–91`` (en dash) matches ``1990-91``."""
    t = (s or "").strip()
    for ch in ("\u2013", "\u2014", "\u2212"):
        t = t.replace(ch, "-")
    return t.strip()


def all_star_logo_start_year_for_row(row: HistoryAllStar) -> int | None:
    """Calendar start year for era-accurate logos (``1989-90`` → ``1989``)."""
    for candidate in (
        (getattr(row, "season_label", None) or "").strip(),
        history_all_star_season_label(row),
    ):
        if not candidate:
            continue
        m = _LABEL_START_YEAR.match(_normalize_all_star_sheet_label(candidate))
        if m:
            return int(m.group(1))
    if row.season is not None and row.season.start_year is not None:
        return int(row.season.start_year)
    return None


def history_all_star_season_label(row: HistoryAllStar) -> str:
    """Display season: stored ``season_label``, then ``sheet_season=`` in notes, else Season label token."""
    sl = (getattr(row, "season_label", None) or "").strip()
    if sl:
        return sl
    n = (row.notes or "").strip()
    for piece in n.split(";"):
        p = piece.strip()
        if p.lower().startswith("sheet_season="):
            return p.split("=", 1)[1].strip()
    if row.season and row.season.label:
        m = _SEASON_TOKEN.search(row.season.label)
        if m:
            return m.group(1)
        return row.season.label.strip()
    return ""


def _season_token_from_sheet_label(lab: str) -> str | None:
    """Return canonical ``YYYY-YY`` token if ``lab`` denotes an all-star season key."""
    n = _normalize_all_star_sheet_label(lab)
    if _LABEL_START_YEAR.match(n):
        return n
    m2 = _SEASON_TOKEN.search(n)
    return m2.group(1) if m2 else None


def _season_ids_for_all_star_label(session: Session, label: str) -> list[int]:
    """Resolve ``Season.id`` list for a sheet label like ``1990-91``."""
    lab = _normalize_all_star_sheet_label(label or "")
    if not lab:
        return []
    ids = list(session.scalars(select(Season.id).where(Season.label == lab)).all())
    if ids:
        return ids
    ids = list(session.scalars(select(Season.id).where(Season.label.like(f"%{lab}%"))).all())
    if ids:
        return ids
    m = _LABEL_START_YEAR.match(lab)
    if m:
        y = int(m.group(1))
        ids = list(session.scalars(select(Season.id).where(Season.start_year == y)).all())
        if ids:
            return ids
    token = _season_token_from_sheet_label(lab)
    if not token:
        return []
    matched: list[int] = []
    for sid, lbl in session.execute(select(Season.id, Season.label)).all():
        if not lbl:
            continue
        ln = _normalize_all_star_sheet_label(lbl)
        if token in ln:
            matched.append(int(sid))
            continue
        mt = _SEASON_TOKEN.search(ln)
        if mt and mt.group(1) == token:
            matched.append(int(sid))
    return matched


def attach_history_all_star_season_teams(session: Session, rows: list[HistoryAllStar]) -> None:
    """Set ``season_team`` for logo display: roster stats for that season, else career/game inference.

    1. Prefer ``PlayerSkaterStat`` / ``PlayerGoalieStat`` for the ``Season`` matching the All-Star
       label (sim roster team for that year).
    2. Else same batch logic as League History awards (career lines, then game counts by calendar year).
    3. Template prefers ``row.team`` when ``team_id`` is set (sheet club for that selection),
       else ``season_team`` from steps 1–2.
    """
    for r in rows:
        setattr(r, "season_team", None)

    season_team_id_by_row_id: dict[int, int] = {}

    for row in rows:
        if row.player_id is None:
            continue
        lab = history_all_star_season_label(row)
        sids = _season_ids_for_all_star_label(session, lab)
        if not sids:
            continue
        sk = session.scalars(
            select(PlayerSkaterStat)
            .where(
                PlayerSkaterStat.player_id == int(row.player_id),
                PlayerSkaterStat.season_id.in_(sids),
                PlayerSkaterStat.stat_segment == "rs",
                PlayerSkaterStat.team_id.isnot(None),
            )
            .order_by(PlayerSkaterStat.gp.desc())
        ).first()
        if sk is not None and sk.team_id is not None:
            season_team_id_by_row_id[row.id] = int(sk.team_id)
            continue
        gk = session.scalars(
            select(PlayerGoalieStat)
            .where(
                PlayerGoalieStat.player_id == int(row.player_id),
                PlayerGoalieStat.season_id.in_(sids),
                PlayerGoalieStat.stat_segment == "rs",
                PlayerGoalieStat.team_id.isnot(None),
            )
            .order_by(PlayerGoalieStat.gp.desc())
        ).first()
        if gk is not None and gk.team_id is not None:
            season_team_id_by_row_id[row.id] = int(gk.team_id)

    key_rows: list[tuple[int, int, HistoryAllStar]] = []
    for row in rows:
        if row.id in season_team_id_by_row_id:
            continue
        sy = all_star_logo_start_year_for_row(row)
        if row.player_id is None or sy is None:
            continue
        key_rows.append((int(row.player_id), int(sy), row))

    if key_rows:
        player_ids = sorted({pid for pid, _, _ in key_rows})
        season_years = sorted({sy for _, sy, _ in key_rows})
        by_player_year_team: dict[int, dict[int, dict[int, int]]] = {}
        career_best: dict[tuple[int, int], tuple[int, int | None, int | None]] = {}

        def _add_career_rows(rows_in: list[tuple[object, object, object, object, object]]) -> None:
            for pid_raw, year_raw, gp_raw, team_id_raw, team_fhm_raw in rows_in:
                try:
                    pid = int(pid_raw)
                    year = int(year_raw)
                    gp = int(gp_raw or 0)
                except (TypeError, ValueError):
                    continue
                team_id: int | None
                team_fhm_id: int | None
                try:
                    team_id = int(team_id_raw) if team_id_raw is not None else None
                except (TypeError, ValueError):
                    team_id = None
                try:
                    team_fhm_id = int(team_fhm_raw) if team_fhm_raw is not None else None
                except (TypeError, ValueError):
                    team_fhm_id = None
                k = (pid, year)
                prev = career_best.get(k)
                if prev is None or gp > prev[0]:
                    career_best[k] = (gp, team_id, team_fhm_id)

        def _add_counts(rows_in: list[tuple[object, object, object, object]]) -> None:
            for pid_raw, year_raw, team_id_raw, n_raw in rows_in:
                try:
                    pid = int(pid_raw)
                    year = int(year_raw)
                    team_id = int(team_id_raw)
                    n = int(n_raw)
                except (TypeError, ValueError):
                    continue
                by_player_year_team.setdefault(pid, {}).setdefault(year, {})
                by_player_year_team[pid][year][team_id] = by_player_year_team[pid][year].get(team_id, 0) + n

        sk_career = session.execute(
            select(
                PlayerSkaterCareerLine.player_id,
                PlayerSkaterCareerLine.season_year,
                PlayerSkaterCareerLine.gp,
                PlayerSkaterCareerLine.team_id,
                PlayerSkaterCareerLine.team_fhm_id,
            ).where(
                PlayerSkaterCareerLine.player_id.in_(player_ids),
                PlayerSkaterCareerLine.season_year.in_(season_years),
            )
        ).all()
        _add_career_rows(sk_career)

        gk_career = session.execute(
            select(
                PlayerGoalieCareerLine.player_id,
                PlayerGoalieCareerLine.season_year,
                PlayerGoalieCareerLine.gp,
                PlayerGoalieCareerLine.team_id,
                PlayerGoalieCareerLine.team_fhm_id,
            ).where(
                PlayerGoalieCareerLine.player_id.in_(player_ids),
                PlayerGoalieCareerLine.season_year.in_(season_years),
            )
        ).all()
        _add_career_rows(gk_career)

        sk_games = session.execute(
            select(
                GameSkaterStat.player_id,
                extract("year", Game.game_date),
                GameSkaterStat.team_id,
                func.count(GameSkaterStat.id),
            )
            .join(Game, GameSkaterStat.game_id == Game.id)
            .where(
                GameSkaterStat.player_id.in_(player_ids),
                GameSkaterStat.team_id.isnot(None),
                Game.game_date.isnot(None),
            )
            .group_by(GameSkaterStat.player_id, extract("year", Game.game_date), GameSkaterStat.team_id)
        ).all()
        _add_counts(sk_games)

        gk_games = session.execute(
            select(
                GameGoalieStat.player_id,
                extract("year", Game.game_date),
                GameGoalieStat.team_id,
                func.count(GameGoalieStat.id),
            )
            .join(Game, GameGoalieStat.game_id == Game.id)
            .where(
                GameGoalieStat.player_id.in_(player_ids),
                GameGoalieStat.team_id.isnot(None),
                Game.game_date.isnot(None),
            )
            .group_by(GameGoalieStat.player_id, extract("year", Game.game_date), GameGoalieStat.team_id)
        ).all()
        _add_counts(gk_games)

        team_fhm_ids = sorted(
            {
                int(v[2])
                for v in career_best.values()
                if v[2] is not None and str(v[2]).strip() != ""
            }
        )
        team_by_fhm: dict[int, Team] = {}
        if team_fhm_ids:
            team_by_fhm = {
                int(str(t.fhm_team_id).strip()): t
                for t in session.scalars(select(Team).where(Team.fhm_team_id.in_(team_fhm_ids))).all()
                if t.fhm_team_id is not None and str(t.fhm_team_id).strip() != ""
            }

        for pid, sy, row in key_rows:
            car = career_best.get((pid, sy))
            if car is not None:
                _, car_team_id, car_team_fhm = car
                if car_team_id is not None:
                    season_team_id_by_row_id[row.id] = car_team_id
                    continue
                if car_team_fhm is not None and car_team_fhm in team_by_fhm:
                    season_team_id_by_row_id[row.id] = team_by_fhm[car_team_fhm].id
                    continue

            team_counts: dict[int, int] = {}
            for yr in (sy, sy + 1):
                for team_id, n in by_player_year_team.get(pid, {}).get(yr, {}).items():
                    team_counts[team_id] = team_counts.get(team_id, 0) + n
            if not team_counts:
                continue
            best_team_id = max(team_counts.items(), key=lambda x: (x[1], -x[0]))[0]
            season_team_id_by_row_id[row.id] = best_team_id

    if not season_team_id_by_row_id:
        return
    teams = {
        t.id: t
        for t in session.scalars(
            select(Team).where(Team.id.in_(sorted(set(season_team_id_by_row_id.values()))))
        ).all()
    }
    for row in rows:
        tid = season_team_id_by_row_id.get(row.id)
        if tid is not None:
            setattr(row, "season_team", teams.get(tid))


def _group_start_year(rows: list[HistoryAllStar]) -> int:
    ys: list[int] = []
    for r in rows:
        if r.season and r.season.start_year is not None:
            ys.append(int(r.season.start_year))
    return max(ys) if ys else 0


def build_history_all_stars_bundle(session: Session, selected_season: str | None) -> dict[str, Any]:
    """Season dropdown + first/second team rows for the League History page."""
    rows = list(
        session.scalars(
            select(HistoryAllStar)
            .options(
                joinedload(HistoryAllStar.season),
                joinedload(HistoryAllStar.player),
                joinedload(HistoryAllStar.team),
            )
            .join(HistoryAllStar.season)
            .order_by(
                Season.start_year.asc(),
                HistoryAllStar.season_label.asc(),
                HistoryAllStar.team_rank.asc(),
                HistoryAllStar.slot.asc(),
            )
        ).all()
    )
    if not rows:
        return {
            "season_labels": [],
            "selected": None,
            "first_team": [],
            "second_team": [],
            "display_title": None,
        }

    by_label: dict[str, list[HistoryAllStar]] = {}
    for r in rows:
        lab = history_all_star_season_label(r)
        if not lab:
            continue
        by_label.setdefault(lab, []).append(r)

    season_labels = sorted(
        by_label.keys(),
        key=lambda lab: (_group_start_year(by_label[lab]), lab),
        reverse=True,
    )

    sel = (selected_season or "").strip()
    if sel not in by_label:
        sel = season_labels[0] if season_labels else None

    pool = by_label.get(sel or "", [])
    first_team = sorted([r for r in pool if r.team_rank == 1], key=lambda r: r.slot)
    second_team = sorted([r for r in pool if r.team_rank == 2], key=lambda r: r.slot)

    attach_history_all_star_season_teams(session, first_team + second_team)

    display_title = f"BOWL — {sel} Season" if sel else None

    return {
        "season_labels": season_labels,
        "selected": sel,
        "first_team": first_team,
        "second_team": second_team,
        "display_title": display_title,
    }
