from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select

from app.models import Player, PlayerGoalieStat, PlayerSkaterStat, Season, Team, TeamStanding


def build_media_kit_snapshot(
    session,
    *,
    team_id: int,
    season_id: int | None = None,
) -> dict:
    team = session.get(Team, int(team_id))
    if not team:
        return {"ok": False, "message": "Team not found."}

    season = session.get(Season, int(season_id)) if season_id else None
    if season is None:
        season = session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if season is None:
        season = session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    if season is None:
        return {"ok": False, "message": "No seasons available."}

    standing = session.scalar(
        select(TeamStanding).where(
            TeamStanding.season_id == int(season.id),
            TeamStanding.team_id == int(team.id),
        ).limit(1)
    )

    top_skaters = session.scalars(
        select(PlayerSkaterStat)
        .join(Player, PlayerSkaterStat.player_id == Player.id)
        .where(
            PlayerSkaterStat.season_id == int(season.id),
            PlayerSkaterStat.stat_segment == "rs",
            Player.current_team_id == int(team.id),
        )
        .order_by(desc(PlayerSkaterStat.points), desc(PlayerSkaterStat.goals))
        .limit(5)
    ).all()
    skater_player_ids = [int(s.player_id) for s in top_skaters]
    skater_players = (
        {int(p.id): p for p in session.scalars(select(Player).where(Player.id.in_(skater_player_ids))).all()}
        if skater_player_ids
        else {}
    )

    top_goalies = session.scalars(
        select(PlayerGoalieStat)
        .join(Player, PlayerGoalieStat.player_id == Player.id)
        .where(
            PlayerGoalieStat.season_id == int(season.id),
            PlayerGoalieStat.stat_segment == "rs",
            Player.current_team_id == int(team.id),
        )
        .order_by(desc(PlayerGoalieStat.wins), PlayerGoalieStat.gaa.asc())
        .limit(3)
    ).all()
    goalie_player_ids = [int(g.player_id) for g in top_goalies]
    goalie_players = (
        {int(p.id): p for p in session.scalars(select(Player).where(Player.id.in_(goalie_player_ids))).all()}
        if goalie_player_ids
        else {}
    )

    summary = {
        "team_name": team.full_display_name(),
        "team_abbr": str(team.abbreviation or ""),
        "season_label": str(season.label or ""),
        "record": (
            f"{int(standing.w or 0)}-{int(standing.l or 0)}-{int(standing.otl or 0)}"
            if standing
            else "—"
        ),
        "points": int(standing.pts or 0) if standing else 0,
        "gf": int(standing.gf or 0) if standing else 0,
        "ga": int(standing.ga or 0) if standing else 0,
    }
    skater_rows = [
        {
            "name": str(skater_players.get(int(s.player_id)).full_name if skater_players.get(int(s.player_id)) else f"player_id={s.player_id}"),
            "gp": int(s.gp or 0),
            "g": int(s.goals or 0),
            "a": int(s.assists or 0),
            "pts": int(s.points or 0),
        }
        for s in top_skaters
    ]
    goalie_rows = [
        {
            "name": str(goalie_players.get(int(g.player_id)).full_name if goalie_players.get(int(g.player_id)) else f"player_id={g.player_id}"),
            "gp": int(g.gp or 0),
            "w": int(g.wins or 0),
            "gaa": float(g.gaa or 0.0),
            "sv_pct": float(g.save_pct or 0.0),
        }
        for g in top_goalies
    ]
    return {
        "ok": True,
        "summary": summary,
        "top_skaters": skater_rows,
        "top_goalies": goalie_rows,
        "generated_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
        "note": "Static snapshot for media/comms; verify against official league stats before publication.",
    }
