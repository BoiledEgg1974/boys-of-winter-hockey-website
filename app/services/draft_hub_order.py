"""Build Draft Hub slot order from prior-season standings and admin-managed pick ownership."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, joinedload

from app.models import Game, Season, Team, TeamSeasonRecord, TeamStanding
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


def draft_order_prior_year_label(draft_year: int) -> str:
    """Hockey season label for the year that seeds *draft_year* (e.g. 1970-71 for 1971)."""
    prior = int(draft_year) - 1
    return f"{prior}-{(prior + 1) % 100:02d}"


def _main_league_standings_score(league_session: Session, season_id: int) -> tuple[int, int]:
    """(rows with W/L/PTS, total main-league standings rows) for ranking candidate seasons."""
    rows = league_session.scalars(
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == int(season_id))
    ).all()
    filtered = [row for row in rows if row.team is not None and is_main_league_team(row.team)]
    with_records = sum(1 for row in filtered if _standing_row_has_record_data(row))
    return (with_records, len(filtered))


def _best_season_with_standings(
    league_session: Session, candidates: list[Season]
) -> Season | None:
    """Prefer the candidate season that actually has imported main-league standings."""
    best: Season | None = None
    best_key = (0, 0)
    for season in candidates:
        key = _main_league_standings_score(league_session, int(season.id))
        if key > best_key:
            best = season
            best_key = key
    if best_key[1] == 0:
        return None
    return best


def resolve_prior_season_for_draft(
    league_session: Session, *, draft_year: int | None = None, standings_start_year: int | None = None
) -> Season | None:
    """Season whose standings should seed draft order for *draft_year*.

  Tries, in order:
  1. ``start_year == draft_year - 1`` (completed prior season, e.g. 1968-69 for a 1969 draft)
  2. ``end_year == draft_year`` (same completed year when only the end year is stored)
  3. ``start_year == draft_year`` only when that row has real W/L/PTS (in-progress pool)

  FHM historical imports keep a single rolling season row. When that row has already
  advanced to the new league year, it often has 0-0-0 standings and must not win over
  Team Records / career-line history for the completed prior season.

  When multiple season rows share a year, picks the one with the most record-bearing
  main-league standings rows.
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

    by_end = [s for s in seasons if s.end_year == year]
    picked = _best_season_with_standings(league_session, by_end)
    if picked is not None:
        rows = league_session.scalars(
            select(TeamStanding).where(TeamStanding.season_id == int(picked.id))
        ).all()
        if any(_standing_row_has_record_data(row) for row in rows):
            return picked

    by_same_start = [s for s in seasons if s.start_year == year]
    picked = _best_season_with_standings(league_session, by_same_start)
    if picked is not None:
        rows = league_session.scalars(
            select(TeamStanding).where(TeamStanding.season_id == int(picked.id))
        ).all()
        if any(_standing_row_has_record_data(row) for row in rows):
            return picked
    return None


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


def _standing_row_has_record_data(row: TeamStanding | DraftOrderStanding) -> bool:
    return any(
        int(getattr(row, attr, 0) or 0) > 0
        for attr in ("gp", "w", "l", "ties", "otl", "pts", "gf", "ga")
    )


def _standings_have_record_data(rows: list[TeamStanding | DraftOrderStanding]) -> bool:
    return any(_standing_row_has_record_data(row) for row in rows)


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


def _draft_order_standing_from_record(rec: TeamSeasonRecord, team: Team) -> DraftOrderStanding:
    wins = int(rec.w or 0)
    ties = int(rec.t_otl or 0)
    pts = int(rec.pts) if rec.pts is not None else wins * 2 + ties
    return DraftOrderStanding(
        team=team,
        gp=int(rec.gp or 0),
        w=wins,
        l=int(rec.l or 0),
        ties=ties,
        pts=pts,
        gf=int(rec.gf or 0),
        ga=int(rec.ga or 0),
    )


def _standings_from_team_season_records(
    league_session: Session, year_label: str
) -> list[DraftOrderStanding]:
    """Completed prior-season rows from Team Records (CSV or synced import)."""
    from app.services.team_records import _load_records_for_year

    recs = _load_records_for_year(league_session, year_label)
    rows: list[DraftOrderStanding] = []
    for rec in recs:
        if rec.team is None or not is_main_league_team(rec.team):
            continue
        rows.append(_draft_order_standing_from_record(rec, rec.team))
    return sorted(rows, key=_standing_worst_first_key)


def _standings_from_career_year(
    league_session: Session, season_year: int
) -> list[DraftOrderStanding]:
    """Rebuild last season's table from NHL/BOWL career lines when TeamStanding was wiped."""
    from app.services.all_time_records import bowl_nhl_league_ids
    from app.services.team_season_record_sync import (
        _aggregate_career_year,
        _import_season_aggs_complete,
    )

    league_ids = bowl_nhl_league_ids(league_session) or (0,)
    aggs = _aggregate_career_year(
        league_session,
        season_year=int(season_year),
        league_ids=league_ids,
        team_meta={},
    )
    if not _import_season_aggs_complete(aggs):
        return []
    rows: list[DraftOrderStanding] = []
    for agg in aggs.values():
        if agg.team_id is None:
            continue
        team = league_session.get(Team, int(agg.team_id))
        if team is None or not is_main_league_team(team):
            continue
        rows.append(
            DraftOrderStanding(
                team=team,
                gp=int(agg.gp),
                w=int(agg.w),
                l=int(agg.l),
                ties=int(agg.otl),
                pts=int(agg.pts),
                gf=int(agg.gf),
                ga=int(agg.ga),
            )
        )
    return sorted(rows, key=_standing_worst_first_key)


def _with_new_franchise_slots(
    league_session: Session, standings: list[TeamStanding | DraftOrderStanding]
) -> list[TeamStanding | DraftOrderStanding]:
    """Expansion clubs that did not play last season pick first (worst record).

    Same club filter as the pick-ownership grid (main-league teams only), on every
    hockey site.
    """
    have_ids = {int(row.team.id) for row in standings if row.team is not None}
    missing: list[DraftOrderStanding] = []
    for team in league_session.scalars(select(Team).order_by(Team.id.asc())).all():
        if not isinstance(team, Team):
            continue
        if not is_main_league_team(team) or int(team.id) in have_ids:
            continue
        missing.append(DraftOrderStanding(team=team))
    missing.sort(key=lambda row: (row.team.full_display_name() if row.team else "").lower())
    return [*missing, *standings]


def default_picks_per_round(league_session: Session) -> int:
    """One pick per current main-league club (Historical 14, Relegation 24, Cap 30, …)."""
    n = sum(
        1
        for team in league_session.scalars(select(Team)).all()
        if isinstance(team, Team) and is_main_league_team(team)
    )
    return max(1, n)


def standings_worst_to_best_for_draft_year(
    league_session: Session, draft_year: int
) -> tuple[list[TeamStanding | DraftOrderStanding], str]:
    """Worst-to-best original draft order for *draft_year* on any hockey site.

    Shared ruleset for Historical, Relegation, and Cap:
    1. Last season (``draft_year - 1``) standings, worst record first.
    2. If FHM has already rolled the single season row to 0-0-0, use Team Records,
       then NHL/BOWL career-line totals for that prior year.
    3. Clubs that did not play last season (expansion) are inserted at the top.
    4. Never rank a 0-0-0 current-year table (that sorts alphabetically).
    """
    year = int(draft_year)
    label = draft_order_prior_year_label(year)
    prior_year = year - 1

    season = resolve_prior_season_for_draft(league_session, draft_year=year)
    if season is not None:
        rows = main_league_standings_worst_to_best(league_session, season)
        if _standings_have_record_data(rows):
            season_start = int(season.start_year) if season.start_year is not None else None
            if season_start == prior_year or season_start is None:
                return _with_new_franchise_slots(league_session, rows), season_display_label(season)

    record_rows = _standings_from_team_season_records(league_session, label)
    if record_rows:
        return _with_new_franchise_slots(league_session, record_rows), label

    career_rows = _standings_from_career_year(league_session, prior_year)
    if career_rows:
        return _with_new_franchise_slots(league_session, career_rows), label

    if season is not None:
        rows = main_league_standings_worst_to_best(league_session, season)
        if _standings_have_record_data(rows):
            return _with_new_franchise_slots(league_session, rows), season_display_label(season)
    return [], label


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
    standings, standings_label = standings_worst_to_best_for_draft_year(
        league_session, draft_year
    )
    picks_per_round = max(1, int(draft.picks_per_round))
    rounds = max(1, int(draft.rounds))
    if not standings:
        return (
            0,
            f"No prior-season standings found for draft timeline {draft_year} "
            f"({standings_label}). Import last season or Team Records and try again.",
            {},
        )
    if len(standings) < picks_per_round:
        return (
            0,
            f"Need at least {picks_per_round} teams in {standings_label} standings; "
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
        "standings_season_label": standings_label,
        "standings_start_year": int(draft_year - 1),
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


def generate_draft_order_from_ranking(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    draft: LeagueDraft,
    ranking_team_ids: list[int],
    preserve_boost_tiers: dict[int, str] | None = None,
    preserve_penalty_picks: set[int] | None = None,
) -> tuple[int, str | None, dict[str, object]]:
    """Replace slots using an admin worst-to-first original-franchise ranking.

    Per-round owners come from the admin-managed ownership grid, same as
    ``generate_draft_order_from_prior_season``.
    """
    slug = str(league_slug or "").strip()
    if not slug:
        return 0, "Missing league slug.", {}
    if str(draft.status or "") != "setup":
        return 0, "Draft order can only be generated while the draft is in setup.", {}

    picks_per_round = max(1, int(draft.picks_per_round))
    rounds = max(1, int(draft.rounds))
    seen: set[int] = set()
    ordered_ids: list[int] = []
    for raw in ranking_team_ids:
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid in seen:
            continue
        seen.add(tid)
        ordered_ids.append(tid)
    if len(ordered_ids) < picks_per_round:
        return (
            0,
            f"Rank at least {picks_per_round} unique teams (worst record first).",
            {},
        )

    teams_by_id: dict[int, Team] = {}
    for team in league_session.scalars(select(Team).where(Team.id.in_(ordered_ids))).all():
        if isinstance(team, Team):
            teams_by_id[int(team.id)] = team
    missing = [tid for tid in ordered_ids[:picks_per_round] if tid not in teams_by_id]
    if missing:
        return 0, "One or more ranked teams were not found.", {}

    draft_year = int(draft.timeline_year)
    ownership = pick_ownership_lookup(site_session, league_slug=slug, draft_year=draft_year)
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
            original_team_id = int(ordered_ids[pick_no - 1])
            team = teams_by_id[original_team_id]
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
        "draft_year": draft_year,
        "traded_count": traded_count,
        "missing_fhm": missing_fhm,
        "has_ownership_rows": has_ownership_rows,
        "rounds": rounds,
        "picks_per_round": picks_per_round,
        "auto_penalties_applied": int(auto_penalties_applied),
        "strike_warnings": strike_warnings,
        "source": "admin_ranking",
    }
    return created, None, summary
