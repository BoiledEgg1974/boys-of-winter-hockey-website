"""Homepage League Pulse module payload."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.models import Team, TeamStanding
from app.services.draft_hub_state import featured_draft
from app.services.draft_hub_tracker import build_draft_hub_tracker
from app.services.homepage_dashboard import (
    build_conf_cutoff_map,
    build_trending_players,
    build_trending_teams,
    league_calendar_anchor_date,
    pick_game_of_the_night,
    pick_next_game_to_watch,
)
from app.services.milestones import build_milestone_sections
from app.services.seasons import get_current_season, season_with_imported_data_fallback
from app.services.trade_market import active_buying_rows, active_selling_rows
from app.logo_urls import team_logo_url_for_team


def build_league_pulse_payload(session, *, league_slug: str) -> dict[str, Any]:
    """Aggregate live league storylines for homepage League Pulse card."""
    canonical = get_current_season()
    season = season_with_imported_data_fallback(session, canonical) if canonical else None
    logo_sy = int(season.start_year) if season and getattr(season, "start_year", None) else None
    out: dict[str, Any] = {
        "spotlight_game": None,
        "hot_team": None,
        "cold_team": None,
        "player_on_fire": None,
        "milestone_watch": [],
        "trade_market_activity": None,
        "draft_pick_value_leader": None,
    }
    if not season:
        return out

    sid = int(season.id)
    standings_by_team = {
        st.team_id: st
        for st in session.scalars(select(TeamStanding).where(TeamStanding.season_id == sid)).all()
    }
    tm_map = {
        tid: t
        for tid in standings_by_team
        if (t := session.get(Team, tid)) is not None
    }
    conf_cutoff = build_conf_cutoff_map(session, sid)
    league_cal = league_calendar_anchor_date(session, sid)
    gotn = pick_game_of_the_night(
        session,
        sid,
        standings_by_team,
        tm_map,
        conf_cutoff,
        league_cal - timedelta(days=7),
        logo_season_year=logo_sy,
    )
    ngw = pick_next_game_to_watch(
        session,
        sid,
        standings_by_team,
        tm_map,
        conf_cutoff,
        league_cal,
        logo_season_year=logo_sy,
    )
    out["spotlight_game"] = gotn or ngw

    trending_teams = build_trending_teams(session, sid, league_cal, logo_season_year=logo_sy)
    hot = (trending_teams.get("hot") or [])[:1]
    cold = (trending_teams.get("cold") or [])[:1]
    if hot:
        out["hot_team"] = hot[0]
    if cold:
        out["cold_team"] = cold[0]

    trending_players = build_trending_players(
        session, sid, "rs", league_cal, limit=3, logo_season_year=logo_sy
    )
    players = trending_players.get("hot") or trending_players.get("players") or []
    if players:
        out["player_on_fire"] = players[0]

    skater_sections, _ = build_milestone_sections(session, split="rs")
    milestone_rows: list[dict[str, Any]] = []
    for sec in skater_sections[:2]:
        for row in (sec.rows or [])[:2]:
            milestone_rows.append(
                {
                    "player": row.player.full_name,
                    "player_id": int(row.player.id),
                    "label": sec.title,
                    "current": int(row.current_value),
                    "next": int(row.next_milestone),
                    "remaining": int(row.remaining),
                }
            )
    out["milestone_watch"] = milestone_rows[:4]

    selling = active_selling_rows(session, session, league_slug=league_slug)
    buying = active_buying_rows(session, session, league_slug=league_slug)
    if selling or buying:
        updates = [
            r.get("updated_at")
            for r in [*selling, *buying]
            if r.get("updated_at") is not None
        ]
        out["trade_market_activity"] = {
            "selling_count": len(selling),
            "buying_teams": len(buying),
            "recent_updates": max(updates) if updates else None,
        }

    teams = {t.id: t for t in session.scalars(select(Team)).all()}
    fd = featured_draft(session, league_slug)
    tracker = build_draft_hub_tracker(
        session,
        session,
        league_slug=league_slug,
        featured_draft=fd,
        team_by_id=teams,
        team_logo_url=lambda tm, _d: team_logo_url_for_team(tm) if tm else None,
        team_page_url=lambda tm: f"/team/{tm.slug}" if tm and tm.slug else "#",
        draft_hub_url=lambda: "/draft-hub",
        draft_archive_url=lambda: "/draft-hub/archive",
        draft_archive_one_url=lambda _id: "/draft-hub/archive",
    )
    top_val = tracker.get("highest_pick_value") or {}
    if top_val.get("teams"):
        out["draft_pick_value_leader"] = {
            "value": top_val.get("value"),
            "teams": top_val.get("teams"),
            "draft_year": tracker.get("draft_year"),
        }

    return out
