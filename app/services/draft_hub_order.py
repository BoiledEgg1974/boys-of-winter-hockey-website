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


def _main_league_standings_count(league_session: Session, season_id: int) -> int:
    """How many main-league team standings rows exist for a season."""
    rows = league_session.scalars(
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == int(season_id))
    ).all()
    return sum(1 for row in rows if row.team is not None and is_main_league_team(row.team))


def _best_season_with_standings(
    league_session: Session, candidates: list[Season]
) -> Season | None:
    """Prefer the candidate season that actually has imported main-league standings."""
    best: Season | None = None
    best_count = 0
    for season in candidates:
        count = _main_league_standings_count(league_session, int(season.id))
        if count > best_count:
            best = season
            best_count = count
    return best


def resolve_prior_season_for_draft(
    league_session: Session, *, draft_year: int | None = None, standings_start_year: int | None = None
) -> Season | None:
    """Season whose standings should seed draft order for *draft_year*.

  Tries, in order:
  1. ``start_year == draft_year - 1`` (completed prior season, e.g. 1968-69 for a 1969 draft)
  2. ``start_year == draft_year`` (active season pool, e.g. Fantasy/Cap when timeline uses start year)
  3. ``end_year == draft_year``

  When multiple season rows share a year, picks the one with the most main-league standings rows.
  """
    year = int(draft_year if draft_year is not None else (standings_start_year or 0) + 1)
    if year <= 0:
        return None
    prior_start = year - 1

    seasons = list(league_session.scalars(select(Season).order_by(Season.id.asc())).all())
    if not seasons:
        return None

    by_prior_start = [s for s in seasons if s.start_year == prior_start]
    picked = _best_season_with_standings(league_session, by_prior_start)
    if picked is not None:
        return picked

    by_same_start = [s for s in seasons if s.start_year == year]
    picked = _best_season_with_standings(league_session, by_same_start)
    if picked is not None:
        return picked

    by_end = [s for s in seasons if s.end_year == year]
    return _best_season_with_standings(league_session, by_end)


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
    season = resolve_prior_season_for_draft(league_session, draft_year=draft_year)
    if season is None:
        return (
            0,
            f"No league season with standings found for draft timeline {draft_year}.",
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
        "standings_start_year": int(season.start_year or draft_year - 1),
        "draft_year": draft_year,
        "traded_count": traded_count,
        "missing_fhm": missing_fhm,
        "has_ownership_csv": has_ownership_csv,
        "rounds": rounds,
        "picks_per_round": picks_per_round,
    }
    return created, None, summary
