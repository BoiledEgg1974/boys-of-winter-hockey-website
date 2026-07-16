"""Build Draft Hub slot order from prior-season standings and admin-managed pick ownership."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Game, Season, Team, TeamStanding
from app.services.cap_strike_penalties import apply_cycle_strikes_to_slots
from app.services.draft_pick_ownership import draft_pick_ownership_exists
from app.services.roster_team import is_main_league_team
from app.services.seasons import season_display_label
from app.site_models import DraftPickOwnershipYear, LeagueDraft, LeagueDraftSlot, TradeMarketDraftPickOwnership


@dataclass(frozen=True)
class DraftOrderStanding:
    """Minimal standings row used to seed draft order."""

    team: Team
    gp: int = 0
    w: int = 0
    l: int = 0
    ties: int = 0
    otl: int = 0
    pts: int = 0
    gf: int = 0
    ga: int = 0


def _standing_worst_first_key(row: TeamStanding | DraftOrderStanding) -> tuple[float, float, float, float, str]:
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
  2. ``start_year == draft_year`` (active season pool, e.g. Relegation/Cap when timeline uses start year)
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
) -> list[TeamStanding | DraftOrderStanding]:
    rows = list(
        league_session.scalars(
            select(TeamStanding)
            .options(joinedload(TeamStanding.team))
            .where(TeamStanding.season_id == int(season.id))
        ).all()
    )
    filtered = [r for r in rows if r.team is not None and is_main_league_team(r.team)]
    if _standings_have_record_data(filtered):
        return sorted(filtered, key=_standing_worst_first_key)
    derived = _derive_main_league_standings_from_games(league_session, season)
    if derived:
        return sorted(derived, key=_standing_worst_first_key)
    return sorted(filtered, key=_standing_worst_first_key)


def _standings_have_record_data(rows: list[TeamStanding]) -> bool:
    for row in rows:
        if any(
            int(getattr(row, attr, 0) or 0) > 0
            for attr in ("gp", "w", "l", "ties", "otl", "pts", "gf", "ga")
        ):
            return True
    return False


def _derive_main_league_standings_from_games(
    league_session: Session, season: Season
) -> list[DraftOrderStanding]:
    teams = list(league_session.scalars(select(Team)).all())
    main_team_by_id = {int(t.id): t for t in teams if is_main_league_team(t)}
    if not main_team_by_id:
        return []
    stats = {
        tid: {"gp": 0, "w": 0, "l": 0, "ties": 0, "otl": 0, "pts": 0, "gf": 0, "ga": 0}
        for tid in main_team_by_id
    }
    games = league_session.scalars(
        select(Game)
        .where(
            Game.season_id == int(season.id),
            Game.status == "final",
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
        .order_by(Game.game_date.asc(), Game.id.asc())
    ).all()
    has_regular_games = False
    for game in games:
        if str(game.game_type or "").strip().lower() != "regular season":
            continue
        home_id = int(game.home_team_id)
        away_id = int(game.away_team_id)
        if home_id not in stats or away_id not in stats:
            continue
        has_regular_games = True
        home_score = int(game.home_score or 0)
        away_score = int(game.away_score or 0)
        home = stats[home_id]
        away = stats[away_id]
        home["gp"] += 1
        away["gp"] += 1
        home["gf"] += home_score
        home["ga"] += away_score
        away["gf"] += away_score
        away["ga"] += home_score
        loser_gets_point = bool(game.went_to_overtime or game.went_to_shootout)
        if home_score > away_score:
            home["w"] += 1
            home["pts"] += 2
            if loser_gets_point:
                away["otl"] += 1
                away["pts"] += 1
            else:
                away["l"] += 1
        elif away_score > home_score:
            away["w"] += 1
            away["pts"] += 2
            if loser_gets_point:
                home["otl"] += 1
                home["pts"] += 1
            else:
                home["l"] += 1
        else:
            home["ties"] += 1
            away["ties"] += 1
            home["pts"] += 1
            away["pts"] += 1
    if not has_regular_games:
        return []
    return [DraftOrderStanding(team=main_team_by_id[tid], **values) for tid, values in stats.items()]


def pick_ownership_lookup(
    site_session: Session,
    *,
    league_slug: str,
    draft_year: int,
) -> dict[tuple[int, int], int]:
    """Map (round, original_team_fhm_id) -> owner_team_id for one draft year.

    Uses the ownership grid for that draft year even when the year panel is
    marked completed (archived), so Draft Hub order generation still applies
    traded picks for past draft years.
    """
    slug = str(league_slug or "").strip()
    if not slug:
        return {}
    rows = site_session.scalars(
        select(TradeMarketDraftPickOwnership)
        .join(
            DraftPickOwnershipYear,
            and_(
                DraftPickOwnershipYear.league_slug == TradeMarketDraftPickOwnership.league_slug,
                DraftPickOwnershipYear.draft_year == TradeMarketDraftPickOwnership.draft_year,
            ),
        )
        .where(
            TradeMarketDraftPickOwnership.league_slug == slug,
            TradeMarketDraftPickOwnership.draft_year == int(draft_year),
            TradeMarketDraftPickOwnership.round <= DraftPickOwnershipYear.round_count,
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
    preserve_penalty_picks: set[int] | None = None,
) -> tuple[int, str | None, dict[str, object]]:
    """
    Replace all slots on *draft* with order from prior-season standings (worst→best)
    and per-round pick ownership from the admin-managed ownership grid.

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
    has_ownership_rows = draft_pick_ownership_exists(site_session, league_slug=slug)
    traded_count = 0
    missing_fhm = 0
    old_tiers = preserve_boost_tiers or {}
    old_penalties = preserve_penalty_picks or set()
    created = 0
    slots_by_orig_round: dict[tuple[int, int], LeagueDraftSlot] = {}

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

            slot = LeagueDraftSlot(
                league_draft_id=int(draft.id),
                overall_pick=overall,
                round=round_no,
                original_team_id=original_team_id,
                team_id=int(owner_team_id),
                boost_tier=old_tiers.get(overall, ""),
                penalty_pick=overall in old_penalties,
            )
            site_session.add(slot)
            slots_by_orig_round[(original_team_id, round_no)] = slot
            created += 1

    auto_penalties_applied = 0
    strike_warnings: list[str] = []
    if slug == "bowl-cap":
        auto_penalties_applied, strike_warnings = apply_cycle_strikes_to_slots(
            site_session,
            league_slug=slug,
            cycle_year=draft_year,
            draft=draft,
            slots_by_orig_round=slots_by_orig_round,
        )

    summary: dict[str, object] = {
        "standings_season_label": season_display_label(season),
        "standings_start_year": int(season.start_year or draft_year - 1),
        "draft_year": draft_year,
        "traded_count": traded_count,
        "missing_fhm": missing_fhm,
        "has_ownership_rows": has_ownership_rows,
        "rounds": rounds,
        "picks_per_round": picks_per_round,
        "auto_penalties_applied": int(auto_penalties_applied),
        "strike_warnings": strike_warnings,
    }
    return created, None, summary
