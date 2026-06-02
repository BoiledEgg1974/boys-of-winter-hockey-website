"""Cap-site strike tracking and draft penalty-pick mapping."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Season, Team
from app.services.draft_pick_ownership import draft_pick_teams_for_grid
from app.site_models import GmRuleStrike, LeagueDraftSlot

STRIKE_TO_ROUND: dict[int, int] = {1: 5, 2: 4, 3: 3}


def active_cycle_year(league_session: Session) -> int:
    """Current in-game cycle keyed to season start year (resets each Oct 1 timeline)."""
    season = league_session.scalar(
        select(Season)
        .where(Season.is_current.is_(True))
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        .limit(1)
    )
    if season is None:
        season = league_session.scalar(
            select(Season).order_by(Season.start_year.desc().nulls_last(), Season.id.desc()).limit(1)
        )
    if season is not None and season.start_year is not None:
        return int(season.start_year)
    return int(datetime.utcnow().year)


def strikes_by_team_for_cycle(
    site_session: Session,
    *,
    league_slug: str,
    cycle_year: int,
) -> dict[int, set[int]]:
    slug = str(league_slug or "").strip()
    rows = list(
        site_session.scalars(
            select(GmRuleStrike).where(
                GmRuleStrike.league_slug == slug,
                GmRuleStrike.cycle_year == int(cycle_year),
                GmRuleStrike.is_active.is_(True),
            )
        ).all()
    )
    out: dict[int, set[int]] = {}
    for row in rows:
        tid = int(row.team_id)
        out.setdefault(tid, set()).add(int(row.strike_no))
    return out


def save_cycle_strikes(
    site_session: Session,
    *,
    league_slug: str,
    cycle_year: int,
    selected: dict[int, set[int]],
    admin_user_id: int | None = None,
) -> tuple[int, int]:
    """Replace all active strikes for a cycle from checkbox payload."""
    slug = str(league_slug or "").strip()
    year = int(cycle_year)
    site_session.execute(
        delete(GmRuleStrike).where(
            GmRuleStrike.league_slug == slug,
            GmRuleStrike.cycle_year == year,
        )
    )
    created = 0
    for team_id, strikes in selected.items():
        for strike_no in sorted({int(x) for x in strikes if int(x) in STRIKE_TO_ROUND}):
            site_session.add(
                GmRuleStrike(
                    league_slug=slug,
                    cycle_year=year,
                    team_id=int(team_id),
                    strike_no=int(strike_no),
                    is_active=True,
                    created_by_user_id=admin_user_id,
                )
            )
            created += 1
    return created, len(selected)


def strike_grid_rows(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    cycle_year: int,
) -> tuple[list[dict], dict[int, set[int]]]:
    teams = draft_pick_teams_for_grid(league_session)
    active = strikes_by_team_for_cycle(site_session, league_slug=league_slug, cycle_year=cycle_year)
    rows: list[dict] = []
    for t in teams:
        rows.append(
            {
                "team_id": int(t.id),
                "team": t,
                "team_name": t.full_display_name(),
                "strikes": active.get(int(t.id), set()),
            }
        )
    return rows, active


def apply_cycle_strikes_to_slots(
    site_session: Session,
    *,
    league_slug: str,
    cycle_year: int,
    draft: object,
    slots_by_orig_round: dict[tuple[int, int], LeagueDraftSlot],
) -> tuple[int, list[str]]:
    """Mark penalty picks from active strikes; return (applied_count, warnings)."""
    warnings: list[str] = []
    applied = 0
    by_team = strikes_by_team_for_cycle(site_session, league_slug=league_slug, cycle_year=cycle_year)
    team_ids = sorted(by_team.keys())
    names: dict[int, str] = {}
    if team_ids:
        for tm in site_session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            names[int(tm.id)] = tm.full_display_name()
    rounds = max(1, int(getattr(draft, "rounds", 1) or 1))
    for team_id in team_ids:
        strikes = sorted(by_team.get(team_id, set()))
        for strike_no in strikes:
            rnd = STRIKE_TO_ROUND.get(int(strike_no))
            if rnd is None or rnd > rounds:
                continue
            slot = slots_by_orig_round.get((int(team_id), int(rnd)))
            team_name = names.get(int(team_id), f"Team {int(team_id)}")
            if slot is None:
                warnings.append(
                    f"{team_name} Strike {int(strike_no)} could not be applied (no round {int(rnd)} slot found)."
                )
                continue
            if int(slot.team_id) != int(team_id):
                warnings.append(
                    f"{team_name} Strike {int(strike_no)} not applied because they do not own their round {int(rnd)} pick."
                )
                continue
            if not bool(getattr(slot, "penalty_pick", False)):
                slot.penalty_pick = True
                applied += 1
    return applied, warnings

