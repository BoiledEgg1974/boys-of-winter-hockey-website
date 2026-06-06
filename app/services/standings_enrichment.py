"""Standings page enrichment: form, streaks, playoff odds, power rank trends."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import Game, Team, TeamStanding
from app.services.homepage_dashboard import (
    recent_form_last10_map,
    team_momentum_streak_label_from_games,
)
from app.services.postseason_odds import build_postseason_odds_payload
from app.services.power_rank_snapshots import (
    apply_power_rank_trends,
    compute_power_rankings_payload,
    select_power_rank_baseline_map,
)


def _team_games_chrono(session, season_id: int) -> dict[int, list[Game]]:
    games = list(
        session.scalars(
            select(Game)
            .where(Game.season_id == int(season_id), Game.status == "final")
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
        ).all()
    )
    by_team: dict[int, list[Game]] = {}
    for g in games:
        if g.home_team_id:
            by_team.setdefault(int(g.home_team_id), []).append(g)
        if g.away_team_id:
            by_team.setdefault(int(g.away_team_id), []).append(g)
    return by_team


def build_standings_row_context(
    session,
    *,
    season_id: int,
    standings_rows: list[TeamStanding],
    league_slug: str,
    logo_season_year: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Per-team extras keyed by team id for standings table rows."""
    if not standings_rows:
        return {}
    team_ids = [int(st.team_id) for st in standings_rows if st.team_id]
    recent = recent_form_last10_map(session, int(season_id))
    games_by_team = _team_games_chrono(session, int(season_id))

    teams_by_id = {
        int(st.team.id): st.team
        for st in standings_rows
        if st.team is not None
    }
    playoff_payload = build_postseason_odds_payload(session, int(season_id), teams_by_id)
    playoff_by_slug: dict[str, dict[str, float]] = (
        playoff_payload.get("by_slug", {}) if playoff_payload else {}
    )

    pr_rows = build_standings_power_rankings(
        session,
        season_id=int(season_id),
        league_slug=league_slug,
        logo_season_year=logo_season_year,
    )
    trend_by_slug = {
        str(r.get("slug") or ""): str(r.get("trend_dir") or "")
        for r in pr_rows
        if r.get("slug")
    }

    out: dict[int, dict[str, Any]] = {}
    for st in standings_rows:
        tid = int(st.team_id)
        tm = st.team
        slug = (tm.slug or "").strip() if tm else ""
        form = recent.get(tid, {})
        streak_label, streak_n = team_momentum_streak_label_from_games(
            tid, games_by_team.get(tid, [])
        )
        po = playoff_by_slug.get(slug, {}) if slug else {}
        trend_dir = trend_by_slug.get(slug, "") if slug else ""
        out[tid] = {
            "last10": form.get("last10"),
            "last10_wins": int(form.get("last10_wins", 0) or 0),
            "last10_losses": int(form.get("last10_losses", 0) or 0),
            "streak_label": streak_label,
            "streak_n": int(streak_n or 0),
            "playoff_pct": po.get("playoffs"),
            "division_pct": po.get("division"),
            "conference_pct": po.get("conference"),
            "trend_dir": trend_dir or None,
        }
    return out


def build_standings_power_rankings(
    session,
    *,
    season_id: int,
    league_slug: str,
    logo_season_year: int | None = None,
) -> list[dict[str, Any]]:
    """Power ranking rows with trend vs last import snapshot."""
    pr = compute_power_rankings_payload(
        session,
        season_id=int(season_id),
        segment="rs",
        logo_season_year=logo_season_year,
    )
    teams = list(pr.get("teams") or [])
    baseline = select_power_rank_baseline_map(league_slug, teams)
    apply_power_rank_trends(teams, baseline)
    return teams
