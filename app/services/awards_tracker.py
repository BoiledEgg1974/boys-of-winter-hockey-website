from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from app.site_models import AwardsVoteBallot, AwardsVotingCycle


def create_voting_cycle(
    session,
    *,
    league_slug: str,
    season_label: str,
    title: str,
    created_by_user_id: int | None,
) -> AwardsVotingCycle:
    row = AwardsVotingCycle(
        league_slug=league_slug,
        season_label=season_label.strip(),
        title=title.strip(),
        status="open",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def list_cycles(session, *, league_slug: str, limit: int = 50) -> list[AwardsVotingCycle]:
    return session.scalars(
        select(AwardsVotingCycle)
        .where(AwardsVotingCycle.league_slug == league_slug)
        .order_by(AwardsVotingCycle.created_at.desc(), AwardsVotingCycle.id.desc())
        .limit(max(1, int(limit)))
    ).all()


def tally_cycle_ballots(session, *, league_slug: str, cycle_id: int) -> list[dict]:
    ballots = session.scalars(
        select(AwardsVoteBallot).where(
            AwardsVoteBallot.league_slug == league_slug,
            AwardsVoteBallot.cycle_id == int(cycle_id),
        )
    ).all()
    grouped: dict[tuple[str, str], dict] = defaultdict(lambda: {"points": 0, "votes": 0})
    for b in ballots:
        key = (str(b.award_key or ""), str(b.candidate_ref or ""))
        grouped[key]["points"] += int(b.points_value or 0)
        grouped[key]["votes"] += 1
    rows = []
    for (award_key, candidate_ref), agg in grouped.items():
        rows.append(
            {
                "award_key": award_key,
                "candidate_ref": candidate_ref,
                "votes": int(agg["votes"]),
                "points": int(agg["points"]),
            }
        )
    rows.sort(key=lambda r: (r["award_key"], -r["points"], -r["votes"], r["candidate_ref"]))
    return rows
