"""Team page dashboard strip: record, form, ranks, leaders, salary."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, PlayerContract, Team, TeamSeasonAggregate, TeamStanding
from app.services.homepage_dashboard import recent_form_last10_map, team_momentum_streak_label_from_games
from app.services.standings import standings_for_season


def _team_games_chrono(session, season_id: int, team_id: int) -> list[Game]:
    return list(
        session.scalars(
            select(Game)
            .where(
                Game.season_id == int(season_id),
                Game.status == "final",
                (Game.home_team_id == int(team_id)) | (Game.away_team_id == int(team_id)),
            )
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
        ).all()
    )


def _dense_rank(pairs: list[tuple[int, float]], high_good: bool) -> dict[int, int]:
    if not pairs:
        return {}
    ordered = sorted(pairs, key=lambda x: x[1], reverse=high_good)
    out: dict[int, int] = {}
    rank = 0
    last_val = None
    for tid, val in ordered:
        if last_val is None or val != last_val:
            rank += 1
            last_val = val
        out[int(tid)] = int(rank)
    return out


def build_team_dashboard_strip(
    session,
    *,
    team: Team,
    season,
    standing: TeamStanding | None,
    league_slug: str,
) -> dict[str, Any]:
    """Compact franchise dashboard payload for team page hero."""
    if not season or not team:
        return {}
    tid = int(team.id)
    sid = int(season.id)
    all_st = standings_for_season(season)
    gf_pairs = [(int(s.team_id), float(s.gf or 0)) for s in all_st if s.team_id]
    ga_pairs = [(int(s.team_id), float(s.ga or 0)) for s in all_st if s.team_id]
    gf_rank = _dense_rank(gf_pairs, True).get(tid)
    ga_rank = _dense_rank(ga_pairs, False).get(tid)

    agg = session.scalar(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == sid,
            TeamSeasonAggregate.team_id == tid,
            TeamSeasonAggregate.segment == "rs",
        ).limit(1)
    )
    pp_pct = None
    pk_pct = None
    if agg and agg.pp_chances and agg.pp_chances > 0 and agg.pp_goals is not None:
        pp_pct = round(100.0 * float(agg.pp_goals) / float(agg.pp_chances), 1)
    if agg and agg.sh_chances and agg.sh_chances > 0 and agg.pk_goals_against is not None:
        pk_pct = round(100.0 - (100.0 * float(agg.pk_goals_against) / float(agg.sh_chances)), 1)

    pp_pairs: list[tuple[int, float]] = []
    for s in all_st:
        a = session.scalar(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == sid,
                TeamSeasonAggregate.team_id == int(s.team_id),
                TeamSeasonAggregate.segment == "rs",
            ).limit(1)
        )
        if a and a.pp_chances and a.pp_chances > 0 and a.pp_goals is not None:
            pp_pairs.append(
                (int(s.team_id), 100.0 * float(a.pp_goals) / float(a.pp_chances))
            )
    pp_rank = _dense_rank(pp_pairs, True).get(tid) if pp_pairs else None

    pk_pairs: list[tuple[int, float]] = []
    for s in all_st:
        a = session.scalar(
            select(TeamSeasonAggregate).where(
                TeamSeasonAggregate.season_id == sid,
                TeamSeasonAggregate.team_id == int(s.team_id),
                TeamSeasonAggregate.segment == "rs",
            ).limit(1)
        )
        if a and a.sh_chances and a.sh_chances > 0 and a.pk_goals_against is not None:
            pk_pairs.append(
                (int(s.team_id), 100.0 - (100.0 * float(a.pk_goals_against) / float(a.sh_chances)))
            )
    pk_rank = _dense_rank(pk_pairs, True).get(tid) if pk_pairs else None

    recent = recent_form_last10_map(session, sid).get(tid, {})
    streak_label, streak_n = team_momentum_streak_label_from_games(
        tid, _team_games_chrono(session, sid, tid)
    )

    top_scorer_row = session.execute(
        select(
            Player.id,
            Player.full_name,
            func.coalesce(func.sum(GameSkaterStat.goals + GameSkaterStat.assists), 0).label("pts"),
        )
        .join(GameSkaterStat, GameSkaterStat.player_id == Player.id)
        .join(Game, Game.id == GameSkaterStat.game_id)
        .where(
            Game.season_id == sid,
            (Game.home_team_id == tid) | (Game.away_team_id == tid),
            Player.current_team_id == tid,
        )
        .group_by(Player.id, Player.full_name)
        .order_by(func.coalesce(func.sum(GameSkaterStat.goals + GameSkaterStat.assists), 0).desc())
        .limit(1)
    ).first()
    top_scorer = None
    if top_scorer_row:
        top_scorer = session.get(Player, int(top_scorer_row[0]))

    top_goalie_row = session.execute(
        select(
            Player.id,
            Player.full_name,
            func.coalesce(func.sum(GameGoalieStat.saves), 0).label("sv"),
        )
        .join(GameGoalieStat, GameGoalieStat.player_id == Player.id)
        .join(Game, Game.id == GameGoalieStat.game_id)
        .where(
            Game.season_id == sid,
            (Game.home_team_id == tid) | (Game.away_team_id == tid),
            Player.current_team_id == tid,
        )
        .group_by(Player.id, Player.full_name)
        .order_by(func.coalesce(func.sum(GameGoalieStat.saves), 0).desc())
        .limit(1)
    ).first()
    top_goalie = None
    if top_goalie_row:
        top_goalie = session.get(Player, int(top_goalie_row[0]))

    salary_total = None
    if league_slug == "bowl-cap":
        salary_total = session.scalar(
            select(func.coalesce(func.sum(PlayerContract.average_salary), 0))
            .join(Player, Player.id == PlayerContract.player_id)
            .where(Player.current_team_id == tid, Player.retired.is_(False))
        )

    from app.services.postseason_odds import build_postseason_odds_payload

    teams_by_id = {int(t.id): t for t in session.scalars(select(Team)).all()}
    po = build_postseason_odds_payload(session, sid, teams_by_id)
    playoff_pct = None
    if po and team.slug:
        playoff_pct = (po.get("by_slug") or {}).get(team.slug, {}).get("playoffs")

    return {
        "record": (
            f"{standing.w}-{standing.l}-{standing.ties}-{standing.otl}"
            if standing
            else None
        ),
        "pts": int(standing.pts) if standing and standing.pts is not None else None,
        "last10": recent.get("last10"),
        "streak_label": streak_label,
        "streak_n": int(streak_n or 0),
        "gf_rank": gf_rank,
        "ga_rank": ga_rank,
        "pp_rank": pp_rank,
        "pk_rank": pk_rank,
        "pp_pct": pp_pct,
        "pk_pct": pk_pct,
        "top_scorer": top_scorer.full_name if top_scorer else None,
        "top_scorer_id": int(top_scorer.id) if top_scorer else None,
        "top_goalie": top_goalie.full_name if top_goalie else None,
        "top_goalie_id": int(top_goalie.id) if top_goalie else None,
        "salary_total": int(salary_total) if salary_total is not None else None,
        "playoff_pct": playoff_pct,
        "division_rank": None,
    }
