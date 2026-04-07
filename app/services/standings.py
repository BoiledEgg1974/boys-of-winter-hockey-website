from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import Season, Team, TeamSeasonAggregate, TeamStanding, db


def _conference_id_map(rows: list[TeamStanding]) -> dict[str, int]:
    """Infer East/West mapping from available team conference ids when names are missing."""
    ids = sorted(
        {
            int(r.team.fhm_conference_id)
            for r in rows
            if r.team is not None and r.team.fhm_conference_id is not None
        }
    )
    if len(ids) < 2:
        return {}
    return {
        "east": ids[0],
        "west": ids[-1],
        # Classic NHL naming aliases used by Fantasy data.
        "wales": ids[0],
        "campbell": ids[-1],
    }


def standings_for_season(season: Season | None, conference: str | None = None, division: str | None = None):
    if not season:
        return []
    q = (
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season.id)
        .order_by(TeamStanding.pts.desc(), (TeamStanding.gf - TeamStanding.ga).desc())
    )
    rows = db.session.scalars(q).all()
    if conference:
        conf_key = conference.strip().lower()
        by_name = [r for r in rows if (r.conference or "").strip().lower() == conf_key]
        if by_name:
            rows = by_name
        else:
            # Fallback: some imports leave TeamStanding.conference NULL but do set team fhm_conference_id.
            id_map = _conference_id_map(rows)
            target_id = id_map.get(conf_key)
            if target_id is not None:
                rows = [
                    r
                    for r in rows
                    if r.team is not None and r.team.fhm_conference_id == target_id
                ]
            else:
                rows = []
    if division:
        rows = [r for r in rows if (r.division or "") == division]
    return rows


def conferences_for_season(season: Season | None) -> list[str]:
    if not season:
        return []
    q = (
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season.id)
    )
    rows = db.session.scalars(q).all()
    names = sorted({(r.conference or "").strip() for r in rows if (r.conference or "").strip()})
    if names:
        return names
    # Fallback to East/West when conference text is missing but conference ids exist.
    id_map = _conference_id_map(rows)
    out: list[str] = []
    if "east" in id_map:
        out.append("East")
    if "west" in id_map:
        out.append("West")
    return out


def divisions_for_season(season: Season | None) -> list[str]:
    if not season:
        return []
    q = select(TeamStanding.division).where(TeamStanding.season_id == season.id).distinct()
    return sorted({d for d in db.session.scalars(q).all() if d})


def team_aggregate_rows(
    season: Season | None,
    standings_rows: list,
    stat_segment: str,
) -> list[tuple[Team, TeamSeasonAggregate | None]]:
    """Pair teams with TeamSeasonAggregate rows, aligned to standings order when provided."""
    if not season:
        return []
    q = (
        select(TeamSeasonAggregate)
        .options(joinedload(TeamSeasonAggregate.team))
        .where(
            TeamSeasonAggregate.season_id == season.id,
            TeamSeasonAggregate.stat_segment == stat_segment,
        )
    )
    agg_rows = db.session.scalars(q).all()
    by_team_id = {a.team_id: a for a in agg_rows}
    if standings_rows:
        return [(st.team, by_team_id.get(st.team_id)) for st in standings_rows]
    out: list[tuple[Team, TeamSeasonAggregate | None]] = []
    for a in sorted(
        agg_rows,
        key=lambda x: (x.team.name.lower() if x.team else "", x.team_id),
    ):
        if a.team:
            out.append((a.team, a))
    return out
