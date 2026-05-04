"""Aggregate all-time skater and goalie totals from career line tables.

Rows from both active (*rs* / *po*) and retired-player (*retired_rs* / *retired_po*) imports
are deduplicated per season/team/league before summing so totals match the player profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, func, not_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import LeagueMeta, Player, PlayerGoalieCareerLine, PlayerSkaterCareerLine

Split = Literal["rs", "po"]
SortOrder = Literal["asc", "desc"]
RosterFilter = Literal["all", "active", "retired"]

SKATER_SOURCES_RS: tuple[str, ...] = ("rs", "retired_rs")
SKATER_SOURCES_PO: tuple[str, ...] = ("po", "retired_po")
GOALIE_SOURCES_RS: tuple[str, ...] = ("rs", "retired_rs")
GOALIE_SOURCES_PO: tuple[str, ...] = ("po", "retired_po")


def bowl_nhl_league_ids(session: Session) -> tuple[int, ...]:
    """FHM league ids for the main BOWL / NHL sim league only (excludes minors, juniors, etc.)."""
    rows = session.scalars(
        select(LeagueMeta.fhm_league_id).where(
            or_(
                LeagueMeta.fhm_league_id == 0,
                LeagueMeta.abbreviation.in_(("BOWL", "NHL")),
                LeagueMeta.name.ilike("%BOWL%"),
                LeagueMeta.name.ilike("%NHL%"),
            )
        )
    ).all()
    ids = sorted({int(r) for r in rows if r is not None})
    return tuple(ids) if ids else (0,)


def _skater_pos_clause():
    """Non-goalie skaters; NULL position kept (NOT goalie is UNKNOWN when position IS NULL)."""
    goalie_pos = or_(
        Player.position == "G",
        Player.position.like("G %"),
        Player.position.like("G-%"),
    )
    return or_(Player.position.is_(None), not_(goalie_pos))


def skaters_only_position_clause():
    """Public alias for WHERE clauses on skater-only boards (homepage leaders, etc.)."""
    return _skater_pos_clause()


def skater_sources(split: Split) -> tuple[str, ...]:
    return SKATER_SOURCES_RS if split == "rs" else SKATER_SOURCES_PO


def goalie_sources(split: Split) -> tuple[str, ...]:
    return GOALIE_SOURCES_RS if split == "rs" else GOALIE_SOURCES_PO


def default_skater_sort_order(sort_key: str) -> SortOrder:
    """Name sorts A→Z by default; counting stats high→low."""
    return "asc" if sort_key == "player" else "desc"


def default_goalie_sort_order(sort_key: str) -> SortOrder:
    """Lower GA/GAA is better; name A→Z; other stats high→low."""
    if sort_key in ("ga", "gaa", "player"):
        return "asc"
    return "desc"


def _apply_player_roster_filter(stmt, roster: RosterFilter):
    """Limit to active (not retired) or retired players; ``all`` leaves the statement unchanged."""
    if roster == "active":
        return stmt.where(Player.retired.is_(False))
    if roster == "retired":
        return stmt.where(Player.retired.is_(True))
    return stmt


def _career_source_rank_for_split(line, split: Split):
    """Prefer active-season CSV (*rs* / *po*) over retired-player CSV (*retired_rs* / *retired_po*)."""
    if split == "rs":
        return case(
            (line.career_source == "rs", 0),
            (line.career_source == "retired_rs", 1),
            else_=9,
        )
    return case(
        (line.career_source == "po", 0),
        (line.career_source == "retired_po", 1),
        else_=9,
    )


def _asc_desc(col, order: SortOrder):
    return col.asc() if order == "asc" else col.desc()


def _nullable_ord(expr, order: SortOrder):
    o = expr.asc() if order == "asc" else expr.desc()
    return o.nulls_last()


@dataclass
class SkaterAllTimeRow:
    player: Player
    gp: int
    goals: int
    assists: int
    points: int
    plus_minus: int
    pim: int
    pp_goals: int
    pp_assists: int
    sh_goals: int
    sh_assists: int
    gwg: int
    fights: int
    fights_won: int
    hits: int
    gva: int
    tka: int
    sb: int
    shots: int
    career_span: str | None


@dataclass
class GoalieAllTimeRow:
    player: Player
    gp: int
    wins: int
    losses: int
    ties_otl: int
    goals_against: int
    shots_against: int
    shutouts: int
    minutes_played: int
    games_started: int | None
    sv_pct: float | None
    gaa: float | None
    career_span: str | None


def fetch_skater_all_time(
    session: Session,
    split: Split,
    sort: str,
    order: SortOrder,
    roster: RosterFilter = "all",
) -> tuple[list[SkaterAllTimeRow], str, SortOrder]:
    league_ids = bowl_nhl_league_ids(session)
    line = PlayerSkaterCareerLine
    src = skater_sources(split)
    rank_pref = _career_source_rank_for_split(line, split)
    # One row per (player, season, team, league): *rs* vs *retired_rs* (and PO analog) both exist in DB.
    lined = (
        select(
            line.player_id,
            line.season_year,
            line.team_fhm_id,
            line.league_fhm_id,
            line.gp,
            line.goals,
            line.assists,
            line.pim,
            line.plus_minus,
            line.pp_goals,
            line.pp_assists,
            line.sh_goals,
            line.sh_assists,
            line.gwg,
            line.fights,
            line.fights_won,
            line.hits,
            line.gva,
            line.tka,
            line.sb,
            line.shots,
            func.row_number()
            .over(
                partition_by=(
                    line.player_id,
                    line.season_year,
                    line.team_fhm_id,
                    line.league_fhm_id,
                ),
                order_by=(rank_pref, line.id),
            )
            .label("rn"),
        )
        .where(line.career_source.in_(src))
        .where(line.league_fhm_id.in_(league_ids))
    ).subquery()
    sq = (
        select(
            lined.c.player_id.label("pid"),
            func.coalesce(func.sum(lined.c.gp), 0).label("gp"),
            func.coalesce(func.sum(lined.c.goals), 0).label("goals"),
            func.coalesce(func.sum(lined.c.assists), 0).label("assists"),
            func.coalesce(func.sum(lined.c.pim), 0).label("pim"),
            func.coalesce(func.sum(func.coalesce(lined.c.plus_minus, 0)), 0).label("plus_minus"),
            func.coalesce(func.sum(func.coalesce(lined.c.pp_goals, 0)), 0).label("pp_goals"),
            func.coalesce(func.sum(func.coalesce(lined.c.pp_assists, 0)), 0).label("pp_assists"),
            func.coalesce(func.sum(func.coalesce(lined.c.sh_goals, 0)), 0).label("sh_goals"),
            func.coalesce(func.sum(func.coalesce(lined.c.sh_assists, 0)), 0).label("sh_assists"),
            func.coalesce(func.sum(func.coalesce(lined.c.gwg, 0)), 0).label("gwg"),
            func.coalesce(func.sum(func.coalesce(lined.c.fights, 0)), 0).label("fights"),
            func.coalesce(func.sum(func.coalesce(lined.c.fights_won, 0)), 0).label("fights_won"),
            func.coalesce(func.sum(func.coalesce(lined.c.hits, 0)), 0).label("hits"),
            func.coalesce(func.sum(func.coalesce(lined.c.gva, 0)), 0).label("gva"),
            func.coalesce(func.sum(func.coalesce(lined.c.tka, 0)), 0).label("tka"),
            func.coalesce(func.sum(func.coalesce(lined.c.sb, 0)), 0).label("sb"),
            func.coalesce(func.sum(func.coalesce(lined.c.shots, 0)), 0).label("shots"),
            func.min(lined.c.season_year).label("yr_min"),
            func.max(lined.c.season_year).label("yr_max"),
        )
        .where(lined.c.rn == 1)
        .group_by(lined.c.player_id)
    ).subquery()

    pts = sq.c.goals + sq.c.assists
    if sort not in (
        "points",
        "goals",
        "assists",
        "gp",
        "pim",
        "plus_minus",
        "pp_goals",
        "pp_assists",
        "sh_goals",
        "sh_assists",
        "gwg",
        "fights",
        "hits",
        "gva",
        "tka",
        "sb",
        "shots",
        "player",
    ):
        sort = "points"
        order = default_skater_sort_order(sort)
    elif order not in ("asc", "desc"):
        order = default_skater_sort_order(sort)
    order_map = {
        "points": _asc_desc(pts, order),
        "goals": _asc_desc(sq.c.goals, order),
        "assists": _asc_desc(sq.c.assists, order),
        "gp": _asc_desc(sq.c.gp, order),
        "pim": _asc_desc(sq.c.pim, order),
        "plus_minus": _asc_desc(sq.c.plus_minus, order),
        "pp_goals": _asc_desc(sq.c.pp_goals, order),
        "pp_assists": _asc_desc(sq.c.pp_assists, order),
        "sh_goals": _asc_desc(sq.c.sh_goals, order),
        "sh_assists": _asc_desc(sq.c.sh_assists, order),
        "gwg": _asc_desc(sq.c.gwg, order),
        "fights": _asc_desc(sq.c.fights, order),
        "hits": _asc_desc(sq.c.hits, order),
        "gva": _asc_desc(sq.c.gva, order),
        "tka": _asc_desc(sq.c.tka, order),
        "sb": _asc_desc(sq.c.sb, order),
        "shots": _asc_desc(sq.c.shots, order),
        "player": _asc_desc(Player.full_name, order),
    }
    stmt = (
        select(Player)
        .join(sq, Player.id == sq.c.pid)
        .options(joinedload(Player.current_team))
        .where(_skater_pos_clause())
        .where(sq.c.gp > 0)
        .order_by(order_map[sort], Player.id.asc())
        .add_columns(
            sq.c.gp,
            sq.c.goals,
            sq.c.assists,
            sq.c.pim,
            sq.c.plus_minus,
            sq.c.pp_goals,
            sq.c.pp_assists,
            sq.c.sh_goals,
            sq.c.sh_assists,
            sq.c.gwg,
            sq.c.fights,
            sq.c.fights_won,
            sq.c.hits,
            sq.c.gva,
            sq.c.tka,
            sq.c.sb,
            sq.c.shots,
            sq.c.yr_min,
            sq.c.yr_max,
        )
    )
    stmt = _apply_player_roster_filter(stmt, roster)
    rows_out: list[SkaterAllTimeRow] = []
    for row in session.execute(stmt).unique():
        (
            player,
            gp,
            goals,
            assists,
            pim,
            plus_minus,
            pp_goals,
            pp_assists,
            sh_goals,
            sh_assists,
            gwg,
            fights,
            fights_won,
            hits,
            gva,
            tka,
            sb,
            shots,
            yr_min,
            yr_max,
        ) = row
        g, a = int(goals), int(assists)
        if yr_min is not None and yr_max is not None:
            y0, y1 = int(yr_min), int(yr_max)
            span = f"{y0}-{y1}"
        else:
            span = None
        rows_out.append(
            SkaterAllTimeRow(
                player=player,
                gp=int(gp),
                goals=g,
                assists=a,
                points=g + a,
                plus_minus=int(plus_minus),
                pim=int(pim),
                pp_goals=int(pp_goals),
                pp_assists=int(pp_assists),
                sh_goals=int(sh_goals),
                sh_assists=int(sh_assists),
                gwg=int(gwg),
                fights=int(fights),
                fights_won=int(fights_won),
                hits=int(hits),
                gva=int(gva),
                tka=int(tka),
                sb=int(sb),
                shots=int(shots),
                career_span=span,
            )
        )
    return rows_out, sort, order


def fetch_goalie_all_time(
    session: Session,
    split: Split,
    sort: str,
    order: SortOrder,
    roster: RosterFilter = "all",
) -> tuple[list[GoalieAllTimeRow], str, SortOrder]:
    """Totals from goalie career lines (active + retired) in BOWL/NHL leagues only."""
    league_ids = bowl_nhl_league_ids(session)
    line = PlayerGoalieCareerLine
    src = goalie_sources(split)
    rank_pref = _career_source_rank_for_split(line, split)
    lined = (
        select(
            line.player_id,
            line.season_year,
            line.team_fhm_id,
            line.league_fhm_id,
            line.gp,
            line.wins,
            line.losses,
            line.ties_otl,
            line.goals_against,
            line.shots_against,
            line.shutouts,
            line.minutes_played,
            line.games_started,
            func.row_number()
            .over(
                partition_by=(
                    line.player_id,
                    line.season_year,
                    line.team_fhm_id,
                    line.league_fhm_id,
                ),
                order_by=(rank_pref, line.id),
            )
            .label("rn"),
        )
        .where(line.career_source.in_(src))
        .where(line.league_fhm_id.in_(league_ids))
    ).subquery()
    sq = (
        select(
            lined.c.player_id.label("pid"),
            func.coalesce(func.sum(lined.c.gp), 0).label("gp"),
            func.coalesce(func.sum(lined.c.wins), 0).label("wins"),
            func.coalesce(func.sum(lined.c.losses), 0).label("losses"),
            func.coalesce(func.sum(func.coalesce(lined.c.ties_otl, 0)), 0).label("otl_sum"),
            func.coalesce(func.sum(lined.c.goals_against), 0).label("sum_ga"),
            func.coalesce(func.sum(lined.c.shots_against), 0).label("sum_sa"),
            func.coalesce(func.sum(lined.c.shutouts), 0).label("shutouts"),
            func.coalesce(func.sum(func.coalesce(lined.c.minutes_played, 0)), 0).label("sum_min"),
            func.coalesce(func.sum(func.coalesce(lined.c.games_started, 0)), 0).label("gs_sum"),
            func.min(lined.c.season_year).label("yr_min"),
            func.max(lined.c.season_year).label("yr_max"),
        )
        .where(lined.c.rn == 1)
        .group_by(lined.c.player_id)
    ).subquery()

    total_ga = sq.c.sum_ga
    total_sa = sq.c.sum_sa
    total_min = sq.c.sum_min
    sv_expr = case((total_sa > 0, 1.0 - (total_ga / total_sa)), else_=None)
    gaa_expr = case((total_min > 0, total_ga * 60.0 / total_min), else_=None)

    if sort not in (
        "wins",
        "gp",
        "games_started",
        "minutes_played",
        "losses",
        "otl",
        "ga",
        "shots_against",
        "shutouts",
        "sv_pct",
        "gaa",
        "player",
    ):
        sort = "wins"
        order = default_goalie_sort_order(sort)
    elif order not in ("asc", "desc"):
        order = default_goalie_sort_order(sort)
    order_map = {
        "wins": _asc_desc(sq.c.wins, order),
        "gp": _asc_desc(sq.c.gp, order),
        "games_started": _asc_desc(sq.c.gs_sum, order),
        "minutes_played": _asc_desc(sq.c.sum_min, order),
        "losses": _asc_desc(sq.c.losses, order),
        "otl": _asc_desc(sq.c.otl_sum, order),
        "ga": _asc_desc(total_ga, order),
        "shots_against": _asc_desc(sq.c.sum_sa, order),
        "shutouts": _asc_desc(sq.c.shutouts, order),
        "sv_pct": _nullable_ord(sv_expr, order),
        "gaa": _nullable_ord(gaa_expr, order),
        "player": _asc_desc(Player.full_name, order),
    }
    stmt = (
        select(Player)
        .join(sq, Player.id == sq.c.pid)
        .options(joinedload(Player.current_team))
        .where(sq.c.gp > 0)
        .order_by(order_map[sort], Player.id.asc())
        .add_columns(
            sq.c.gp,
            sq.c.wins,
            sq.c.losses,
            sq.c.otl_sum,
            sq.c.sum_ga,
            sq.c.sum_sa,
            sq.c.shutouts,
            sq.c.sum_min,
            sq.c.gs_sum,
            sq.c.yr_min,
            sq.c.yr_max,
        )
    )
    stmt = _apply_player_roster_filter(stmt, roster)
    rows_out: list[GoalieAllTimeRow] = []
    for row in session.execute(stmt).unique():
        (
            player,
            gp,
            wins,
            losses,
            otl_sum,
            sum_ga,
            sum_sa,
            shutouts,
            sum_min,
            gs_sum,
            yr_min,
            yr_max,
        ) = row
        tga, tsa = int(sum_ga), int(sum_sa)
        tmin = int(sum_min)
        sv = (1.0 - (tga / tsa)) if tsa > 0 else None
        gaa = (tga * 60.0 / tmin) if tmin > 0 else None
        gs_int = int(gs_sum) if gs_sum is not None else None
        if yr_min is not None and yr_max is not None:
            y0, y1 = int(yr_min), int(yr_max)
            span = f"{y0}-{y1}"
        else:
            span = None
        rows_out.append(
            GoalieAllTimeRow(
                player=player,
                gp=int(gp),
                wins=int(wins),
                losses=int(losses),
                ties_otl=int(otl_sum or 0),
                goals_against=tga,
                shots_against=tsa,
                shutouts=int(shutouts),
                minutes_played=tmin,
                games_started=gs_int,
                sv_pct=sv,
                gaa=gaa,
                career_span=span,
            )
        )
    return rows_out, sort, order
