"""Draft Hub: eligible prospect pool (undrafted + org-rights exclusions + min/max age cutoffs)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Draft, DraftPick, Player
from app.services.draft_history import nhl_bowl_draft_clause
from app.services.free_agents import bowl_org_rights_player_ids

# Max players returned on the live hub eligible board (default view). Full pool remains for
# picks, counts, and search/position filters — only the unfiltered list is capped.
ELIGIBLE_HUB_BOARD_WINDOW = 100


def age_as_of(birth: date | None, as_of: date) -> int | None:
    if birth is None:
        return None
    return as_of.year - birth.year - ((as_of.month, as_of.day) < (birth.month, birth.day))


@dataclass(frozen=True)
class DraftEligibilityParams:
    timeline_year: int
    min_age_years: int
    min_anchor_month: int
    min_anchor_day: int
    max_age_years: int
    max_anchor_month: int
    max_anchor_day: int


def default_eligibility_for_league(league_slug: str) -> DraftEligibilityParams:
    """Defaults per plan: Cap/Fantasy vs Historical."""
    if league_slug in ("bowl-cap", "bowl-fantasy"):
        return DraftEligibilityParams(
            timeline_year=0,
            min_age_years=18,
            min_anchor_month=9,
            min_anchor_day=15,
            max_age_years=20,
            max_anchor_month=12,
            max_anchor_day=31,
        )
    return DraftEligibilityParams(
        timeline_year=0,
        min_age_years=20,
        min_anchor_month=12,
        min_anchor_day=31,
        max_age_years=21,
        max_anchor_month=12,
        max_anchor_day=31,
    )


def anchor_dates(params: DraftEligibilityParams) -> tuple[date, date]:
    y = params.timeline_year
    return (
        date(y, params.min_anchor_month, params.min_anchor_day),
        date(y, params.max_anchor_month, params.max_anchor_day),
    )


def player_passes_age_rules(birth: date | None, params: DraftEligibilityParams) -> bool:
    if birth is None:
        return False
    min_d, max_d = anchor_dates(params)
    age_min_ref = age_as_of(birth, min_d)
    age_max_ref = age_as_of(birth, max_d)
    if age_min_ref is None or age_max_ref is None:
        return False
    return age_min_ref >= params.min_age_years and age_max_ref <= params.max_age_years


def undrafted_nhl_bowl_player_subquery():
    return (
        select(DraftPick.player_id)
        .join(Draft, DraftPick.draft_id == Draft.id)
        .where(DraftPick.player_id.isnot(None))
        .where(nhl_bowl_draft_clause())
        .distinct()
    )


def eligible_player_ids(session: Session, league_slug: str, params: DraftEligibilityParams) -> list[int]:
    drafted_subq = undrafted_nhl_bowl_player_subquery()
    rights_ids = bowl_org_rights_player_ids(session)
    q_where = [
        Player.retired.is_(False),
        Player.birth_date.isnot(None),
        Player.id.not_in(drafted_subq),
    ]
    if rights_ids:
        q_where.append(Player.id.not_in(rights_ids))
    players = session.scalars(select(Player).where(*q_where)).unique().all()
    out: list[int] = []
    for p in players:
        if player_passes_age_rules(p.birth_date, params):
            out.append(int(p.id))
    return out


def eligible_players_ordered(session: Session, league_slug: str, params: DraftEligibilityParams) -> list[Player]:
    """Players sorted for board rank (potential desc, ability desc, name)."""
    ids = eligible_player_ids(session, league_slug, params)
    if not ids:
        return []
    players = list(session.scalars(select(Player).where(Player.id.in_(ids))).unique().all())

    def sort_key(pl: Player) -> tuple:
        pot = pl.overall_potential
        abi = pl.overall_ability
        pv = float(pot) if pot is not None else float("-inf")
        av = float(abi) if abi is not None else float("-inf")
        return (-pv, -av, (pl.full_name or "").lower(), pl.id)

    players.sort(key=sort_key)
    return players


def board_ranks_map(players: list[Player]) -> dict[str, int]:
    """player_id str -> board rank 1..N (1 is best)."""
    return {str(p.id): i + 1 for i, p in enumerate(players)}
