"""Homepage League Leaders board (RS / PS / PO splits)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from flask import current_app, url_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerGoalieStat, PlayerSkaterStat, Season, Team
from app.services.all_time_records import bowl_nhl_league_ids, skaters_only_position_clause
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.season_team_logo_bundle import dashboard_team_logo_url

_BOWL_SLUGS = frozenset({"bowl-fantasy", "bowl-historical", "bowl-cap"})


def _player_photo_url(pl: Player | None) -> str:
    if not pl:
        return ""
    static_root = Path(current_app.root_path) / "static"
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    if not rel:
        return ""
    return url_for("static", filename=rel)


def build_homepage_leaders_payload(
    session: Session,
    season: Season,
    segment: str,
    *,
    league_slug: str | None = None,
    player_photo_url: Callable[[Player | None], str] | None = None,
) -> dict[str, Any]:
    """Top-10 skater/goalie leader rows for one stat segment."""
    seg = segment if segment in ("rs", "ps", "po") else "rs"
    slug = league_slug or str(current_app.config.get("LEAGUE_SLUG") or "")
    logo_sy: int | None = (
        int(season.start_year) if getattr(season, "start_year", None) is not None else None
    )
    photo_url = player_photo_url or _player_photo_url

    bowl_main_fhm_league_ids: tuple[int, ...] | None = None
    if slug in _BOWL_SLUGS:
        bowl_main_fhm_league_ids = bowl_nhl_league_ids(session)
        if not bowl_main_fhm_league_ids:
            bowl_main_fhm_league_ids = (0,)

    def leader_rows(stat, order_col, limit=10, goalie=False):
        if goalie:
            q = select(PlayerGoalieStat, Player).join(
                Player, PlayerGoalieStat.player_id == Player.id
            )
            if bowl_main_fhm_league_ids is not None:
                q = q.join(Team, PlayerGoalieStat.team_id == Team.id).where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == seg,
                    Team.fhm_league_id.in_(bowl_main_fhm_league_ids),
                )
            else:
                q = q.where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == seg,
                )
            q = q.order_by(order_col.desc(), Player.id.asc()).limit(limit)
            rows = session.execute(q).all()
            out = []
            for pgs, pl in rows:
                tm = session.get(Team, pgs.team_id) if pgs.team_id else None
                out.append(
                    {
                        "player_id": pl.id,
                        "player": pl.full_name,
                        "player_photo_url": photo_url(pl),
                        "team": tm.abbreviation if tm else "",
                        "team_slug": tm.slug if tm else "",
                        "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                        "value": getattr(pgs, order_col.key),
                    }
                )
            return out
        q = select(PlayerSkaterStat, Player).join(
            Player, PlayerSkaterStat.player_id == Player.id
        )
        if bowl_main_fhm_league_ids is not None:
            q = q.join(Team, PlayerSkaterStat.team_id == Team.id).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == seg,
                Team.fhm_league_id.in_(bowl_main_fhm_league_ids),
                skaters_only_position_clause(),
            )
        else:
            q = q.where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == seg,
                skaters_only_position_clause(),
            )
        q = q.order_by(order_col.desc(), Player.id.asc()).limit(limit)
        rows = session.execute(q).all()
        out = []
        for pss, pl in rows:
            tm = session.get(Team, pss.team_id) if pss.team_id else None
            val = getattr(pss, stat)
            out.append(
                {
                    "player_id": pl.id,
                    "player": pl.full_name,
                    "player_photo_url": photo_url(pl),
                    "team": tm.abbreviation if tm else "",
                    "team_slug": tm.slug if tm else "",
                    "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                    "value": val,
                }
            )
        return out

    leaders = {
        "goals": leader_rows("goals", PlayerSkaterStat.goals),
        "assists": leader_rows("assists", PlayerSkaterStat.assists),
        "points": leader_rows("points", PlayerSkaterStat.points),
        "goalie_wins": leader_rows("", PlayerGoalieStat.wins, goalie=True),
        "goalie_shutouts": leader_rows("", PlayerGoalieStat.so, goalie=True),
    }
    return {"segment": seg, "leaders": leaders}
