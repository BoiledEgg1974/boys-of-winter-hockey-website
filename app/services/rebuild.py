"""Recompute derived data after imports."""
from __future__ import annotations

from sqlalchemy import select

from app.db_utils import rebuild_player_fts
from app.models import Game, TeamStanding, db


def recompute_standings_from_games(season_id: int) -> None:
    """
    Rebuild team_standings for a season from completed games.
    Points: regulation/OT/SO win = 2 for winner; OTL/SO loss = 1 for loser (no separate L).
    """
    games = db.session.scalars(
        select(Game).where(Game.season_id == season_id, Game.status == "final")
    ).all()
    if not games:
        return

    standings = db.session.scalars(
        select(TeamStanding).where(TeamStanding.season_id == season_id)
    ).all()
    by_team = {s.team_id: s for s in standings}

    def ensure(team_id: int) -> TeamStanding:
        if team_id not in by_team:
            s = TeamStanding(season_id=season_id, team_id=team_id)
            db.session.add(s)
            by_team[team_id] = s
        return by_team[team_id]

    for tid in list(by_team.keys()):
        st = by_team[tid]
        st.gp = st.w = st.l = st.otl = st.pts = st.gf = st.ga = 0

    for g in games:
        if g.home_score is None or g.away_score is None:
            continue
        ht = ensure(g.home_team_id)
        at = ensure(g.away_team_id)
        ht.gp += 1
        at.gp += 1
        ht.gf += g.home_score
        ht.ga += g.away_score
        at.gf += g.away_score
        at.ga += g.home_score

        ot = g.went_to_overtime or g.went_to_shootout
        if g.home_score > g.away_score:
            ht.w += 1
            ht.pts += 2
            if ot:
                at.otl += 1
                at.pts += 1
            else:
                at.l += 1
        elif g.away_score > g.home_score:
            at.w += 1
            at.pts += 2
            if ot:
                ht.otl += 1
                ht.pts += 1
            else:
                ht.l += 1
        else:
            ht.pts += 1
            at.pts += 1
    db.session.commit()


def refresh_after_import(engine) -> None:
    """Rebuild player search index after data changes."""
    rebuild_player_fts(engine)
