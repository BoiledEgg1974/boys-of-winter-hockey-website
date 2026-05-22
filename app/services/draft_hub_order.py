"""Build Draft Hub slot order from prior-season standings and imported pick ownership."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Season, Team, TeamStanding
from app.services.draft_pick_ownership import draft_pick_ownership_exists
from app.services.roster_team import is_main_league_team
from app.services.seasons import season_display_label
from app.site_models import LeagueDraft, LeagueDraftSlot, TradeMarketDraftPickOwnership


def _standing_worst_first_key(row: TeamStanding) -> tuple[float, float, float, float, str]:
    """Worst record first: PTS asc, W asc, goal diff asc, GF asc, then name."""
    gd = float(int(row.gf or 0) - int(row.ga or 0))
    team_name = ""
    if row.team is not None:
        team_name = row.team.full_display_name().lower()
    return (
        float(int(row.pts or 0)),
        float(int(row.w or 0)),
        gd,
        float(int(row.gf or 0)),
        team_name,
    )


def resolve_prior_season_for_draft(
    league_session: Session, *, standings_start_year: int
) -> Season | None:
    """Season row for the league year immediately before the draft timeline year."""
    year = int(standings_start_year)
    if year <= 0:
        return None
    season = league_session.scalar(
        select(Season).where(Season.start_year == year).order_by(Season.id.desc()).limit(1)
    )
    if season is not None:
        return season
    return league_session.scalar(
        select(Season)
        .where(Season.start_year.is_not(None), Season.start_year < year + 1)
        .order_by(Season.start_year.desc(), Season.id.desc())
        .limit(1)
    )


def main_league_standings_worst_to_best(
    league_session: Session, season: Season
) -> list[TeamStanding]:
    rows = list(
        league_session.scalars(
            select(TeamStanding)
            .options(joinedload(TeamStanding.team))
            .where(TeamStanding.season_id == int(season.id))
        ).all()
    )
    filtered = [r for r in rows if r.team is not None and is_main_league_team(r.team)]
    return sorted(filtered, key=_standing_worst_first_key)


def pick_ownership_lookup(
    site_session: Session,
    *,
    league_slug: str,
    draft_year: int,
) -> dict[tuple[int, int], int]:
    """Map (round, original_team_fhm_id) -> owner_team_id for one draft year."""
    slug = str(league_slug or "").strip()
    if not slug:
        return {}
    rows = site_session.scalars(
        select(TradeMarketDraftPickOwnership).where(
            TradeMarketDraftPickOwnership.league_slug == slug,
            TradeMarketDraftPickOwnership.draft_year == int(draft_year),
        )
    ).all()
    out: dict[tuple[int, int], int] = {}
    for row in rows:
        orig_fhm = int(row.original_team_fhm_id)
        rnd = int(row.round)
        owner_tid = int(row.owner_team_id) if row.owner_team_id else None
        if owner_tid is None:
            continue
        out[(rnd, orig_fhm)] = owner_tid
    return out


def generate_draft_order_from_prior_season(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    draft: LeagueDraft,
    preserve_boost_tiers: dict[int, str] | None = None,
) -> tuple[int, str | None, dict[str, object]]:
    """
    Replace all slots on *draft* with order from prior-season standings (worst→best)
    and per-round pick ownership from ``draft_pick_ownership.csv``.

    Returns (slots_created, error_message, summary_dict).
    """
    slug = str(league_slug or "").strip()
    if not slug:
        return 0, "Missing league slug.", {}
    if str(draft.status or "") != "setup":
        return 0, "Draft order can only be generated while the draft is in setup.", {}

    draft_year = int(draft.timeline_year)
    standings_year = draft_year - 1
    season = resolve_prior_season_for_draft(league_session, standings_start_year=standings_year)
    if season is None:
        return (
            0,
            f"No league season found for standings year {standings_year} "
            f"(prior to draft timeline {draft_year}).",
            {},
        )

    standings = main_league_standings_worst_to_best(league_session, season)
    picks_per_round = max(1, int(draft.picks_per_round))
    rounds = max(1, int(draft.rounds))
    if len(standings) < picks_per_round:
        return (
            0,
            f"Need at least {picks_per_round} teams in {season_display_label(season)} standings; "
            f"found {len(standings)} main-league rows.",
            {},
        )

    base_order = standings[:picks_per_round]
    ownership = pick_ownership_lookup(
        site_session, league_slug=slug, draft_year=draft_year
    )
    has_ownership_csv = draft_pick_ownership_exists(site_session, league_slug=slug)
    traded_count = 0
    missing_fhm = 0
    old_tiers = preserve_boost_tiers or {}
    created = 0

    for round_no in range(1, rounds + 1):
        for pick_no in range(1, picks_per_round + 1):
            overall = ((round_no - 1) * picks_per_round) + pick_no
            standing_row = base_order[pick_no - 1]
            team = standing_row.team
            if team is None:
                continue
            original_team_id = int(team.id)
            owner_team_id = original_team_id
            fhm_raw = str(getattr(team, "fhm_team_id", None) or "").strip()
            if fhm_raw.isdigit():
                orig_fhm = int(fhm_raw)
                owner_team_id = ownership.get((round_no, orig_fhm), original_team_id)
                if owner_team_id != original_team_id:
                    traded_count += 1
            else:
                missing_fhm += 1

            site_session.add(
                LeagueDraftSlot(
                    league_draft_id=int(draft.id),
                    overall_pick=overall,
                    round=round_no,
                    original_team_id=original_team_id,
                    team_id=int(owner_team_id),
                    boost_tier=old_tiers.get(overall, ""),
                )
            )
            created += 1

    summary: dict[str, object] = {
        "standings_season_label": season_display_label(season),
        "standings_start_year": int(season.start_year or standings_year),
        "draft_year": draft_year,
        "traded_count": traded_count,
        "missing_fhm": missing_fhm,
        "has_ownership_csv": has_ownership_csv,
        "rounds": rounds,
        "picks_per_round": picks_per_round,
    }
    return created, None, summary
