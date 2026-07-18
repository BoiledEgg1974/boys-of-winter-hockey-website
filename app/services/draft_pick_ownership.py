"""Admin-managed draft-pick ownership rows for trade and draft workflows."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.models import Season, Team
from app.services.draft_hub_eligibility import draft_eligible_timeline_year_for_league
from app.services.roster_team import is_main_league_team
from app.services.seasons import season_with_imported_data_fallback
from app.site_models import DraftPickOwnershipYear, LeagueDraft, TradeMarketDraftPickOwnership

DRAFT_PICK_DRAG_PREFIX = "dpick"


def draft_pick_drag_key(row_id: int) -> str:
    return f"{DRAFT_PICK_DRAG_PREFIX}:{int(row_id)}"


def parse_draft_pick_drag_key(key: str) -> int | None:
    if not str(key or "").startswith(f"{DRAFT_PICK_DRAG_PREFIX}:"):
        return None
    try:
        return int(str(key).split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def fhm_team_id_to_db_id(league_session: Session) -> dict[int, int]:
    out: dict[int, int] = {}
    for tm in league_session.scalars(select(Team)).all():
        raw = str(getattr(tm, "fhm_team_id", None) or "").strip()
        if raw.isdigit():
            out[int(raw)] = int(tm.id)
    return out


def _team_label(tm: Team | None, fhm_id: int) -> str:
    if tm is not None:
        abbr = (tm.abbreviation or "").strip()
        if abbr:
            return abbr
        return tm.full_display_name()
    return f"Team {fhm_id}"


def describe_draft_pick_row(
    row: TradeMarketDraftPickOwnership,
    *,
    original_team: Team | None = None,
    owner_team: Team | None = None,
) -> str:
    orig = _team_label(original_team, int(row.original_team_fhm_id))
    owner = _team_label(owner_team, int(row.owner_team_fhm_id))
    rnd = int(row.round)
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(rnd, "th")
    year = int(row.draft_year)
    if int(row.original_team_fhm_id) == int(row.owner_team_fhm_id):
        return f"{year} {owner} {rnd}{suffix} Round pick"
    return f"{year} {rnd}{suffix} Round ({orig}) — held by {owner}"


def owned_draft_picks_for_team(
    site_session: Session,
    *,
    league_slug: str,
    team_id: int,
) -> list[TradeMarketDraftPickOwnership]:
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    return list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership)
            .join(
                DraftPickOwnershipYear,
                and_(
                    DraftPickOwnershipYear.league_slug == TradeMarketDraftPickOwnership.league_slug,
                    DraftPickOwnershipYear.draft_year == TradeMarketDraftPickOwnership.draft_year,
                    DraftPickOwnershipYear.status != "completed",
                ),
            )
            .where(
                TradeMarketDraftPickOwnership.league_slug == slug,
                TradeMarketDraftPickOwnership.owner_team_id == int(team_id),
                TradeMarketDraftPickOwnership.round <= DraftPickOwnershipYear.round_count,
            )
            .order_by(
                TradeMarketDraftPickOwnership.draft_year.asc(),
                TradeMarketDraftPickOwnership.round.asc(),
                TradeMarketDraftPickOwnership.original_team_fhm_id.asc(),
            )
        ).all()
    )


def draft_pick_ownership_exists(site_session: Session, *, league_slug: str) -> bool:
    """Return True once admin-managed draft-pick ownership exists for a league."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    row_id = site_session.scalar(
        select(TradeMarketDraftPickOwnership.id)
        .join(
            DraftPickOwnershipYear,
            and_(
                DraftPickOwnershipYear.league_slug == TradeMarketDraftPickOwnership.league_slug,
                DraftPickOwnershipYear.draft_year == TradeMarketDraftPickOwnership.draft_year,
                DraftPickOwnershipYear.status != "completed",
            ),
        )
        .where(TradeMarketDraftPickOwnership.league_slug == slug)
        .limit(1)
    )
    return row_id is not None


def owned_draft_pick_drag_keys(
    site_session: Session, *, league_slug: str, team_id: int
) -> set[str]:
    return {draft_pick_drag_key(r.id) for r in owned_draft_picks_for_team(
        site_session, league_slug=league_slug, team_id=team_id
    )}


def draft_pick_asset_dicts(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    team_id: int,
) -> list[dict[str, Any]]:
    rows = owned_draft_picks_for_team(
        site_session, league_slug=league_slug, team_id=team_id
    )
    team_cache: dict[int, Team | None] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        oid = int(row.original_team_fhm_id)
        owid = int(row.owner_team_fhm_id)
        if oid not in team_cache:
            team_cache[oid] = (
                league_session.get(Team, int(row.original_team_id))
                if row.original_team_id
                else None
            )
        if owid not in team_cache:
            team_cache[owid] = (
                league_session.get(Team, int(row.owner_team_id)) if row.owner_team_id else None
            )
        label = describe_draft_pick_row(
            row,
            original_team=team_cache.get(oid),
            owner_team=team_cache.get(owid),
        )
        out.append(
            {
                "kind": "draft_pick",
                "id": int(row.id),
                "drag_key": draft_pick_drag_key(int(row.id)),
                "label": label,
                "draft_year": int(row.draft_year),
                "round": int(row.round),
                "original_team_fhm_id": oid,
                "section": "draft_pick",
            }
        )
    return out


def draft_pick_owned_by_team(
    site_session: Session,
    *,
    league_slug: str,
    team_id: int,
    drag_key: str,
) -> TradeMarketDraftPickOwnership | None:
    rid = parse_draft_pick_drag_key(drag_key)
    if rid is None:
        return None
    row = site_session.get(TradeMarketDraftPickOwnership, int(rid))
    if row is None:
        return None
    if str(row.league_slug) != str(league_slug):
        return None
    if int(row.owner_team_id or -1) != int(team_id):
        return None
    return row


def draft_pick_teams_for_grid(league_session: Session) -> list[Team]:
    """Main-league teams with numeric FHM ids for ownership grids."""
    rows = list(league_session.scalars(select(Team).order_by(Team.id.asc())).all())
    out: list[Team] = []
    for team in rows:
        raw = str(getattr(team, "fhm_team_id", None) or "").strip()
        if not raw.isdigit():
            continue
        if not is_main_league_team(team):
            continue
        out.append(team)
    return sorted(out, key=_draft_pick_team_sort_key)


def _draft_pick_team_sort_key(team: Team) -> tuple[str, str, int]:
    name = ""
    try:
        name = str(team.full_display_name() or "")
    except Exception:
        name = ""
    if not name.strip():
        name = str(getattr(team, "name", "") or "")
    abbr = str(getattr(team, "abbreviation", "") or "")
    try:
        tid = int(getattr(team, "id", 0) or 0)
    except (TypeError, ValueError):
        tid = 0
    return (name.casefold(), abbr.casefold(), tid)


def list_draft_pick_ownership_year_panels(
    site_session: Session,
    *,
    league_slug: str,
) -> list[DraftPickOwnershipYear]:
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    rows = list(
        site_session.scalars(
            select(DraftPickOwnershipYear)
            .where(DraftPickOwnershipYear.league_slug == slug)
            .order_by(
                DraftPickOwnershipYear.display_order.asc(),
                DraftPickOwnershipYear.draft_year.asc(),
                DraftPickOwnershipYear.id.asc(),
            )
        ).all()
    )
    active = [r for r in rows if str(r.status or "active") != "completed"]
    completed = [r for r in rows if str(r.status or "active") == "completed"]
    return [*active, *completed]


def _next_panel_year(site_session: Session, *, league_slug: str, fallback_start: int) -> int:
    max_year = site_session.scalar(
        select(DraftPickOwnershipYear.draft_year)
        .where(DraftPickOwnershipYear.league_slug == str(league_slug))
        .order_by(DraftPickOwnershipYear.draft_year.desc())
        .limit(1)
    )
    if max_year is not None:
        return int(max_year) + 1
    return int(fallback_start)


def default_draft_pick_ownership_start_year(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> int:
    """Initial panel year from in-game state, never from the real calendar unless no data exists."""
    slug = str(league_slug or "").strip()
    cutoff_year = in_game_draft_ownership_cutoff_year(
        league_session,
        league_slug=slug,
        site_session=site_session,
    )
    if slug:
        draft_year = site_session.scalar(
            select(LeagueDraft.timeline_year)
            .where(
                LeagueDraft.league_slug == slug,
                LeagueDraft.status != "completed",
                LeagueDraft.timeline_year.isnot(None),
            )
            .order_by(LeagueDraft.timeline_year.asc(), LeagueDraft.id.asc())
            .limit(1)
        )
        if draft_year is not None:
            return max(int(draft_year), int(cutoff_year)) if cutoff_year is not None else int(draft_year)
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
    season = season_with_imported_data_fallback(league_session, season) or season
    if season is not None:
        return _ownership_current_draft_year_for_season(season, league_slug=slug)
    return datetime.utcnow().year


def _ownership_current_draft_year_for_season(season: Season, *, league_slug: str) -> int | None:
    """Resolve the draft year represented by the current season's ownership panel."""
    if season.start_year is None and season.end_year is None:
        return None
    slug = str(league_slug or "").strip()
    # Historical and cap ownership labels follow the season they supply: the
    # 1970 draft belongs to 1970-71, so the panel uses the season start year.
    if slug in {"bowl-historical", "bowl-cap"} and season.start_year is not None:
        return int(season.start_year)
    fallback = season.end_year or season.start_year
    if fallback is None:
        return None
    return draft_eligible_timeline_year_for_league(
        slug,
        season.start_year,
        season.end_year,
        int(fallback),
    )


def in_game_draft_ownership_cutoff_year(
    league_session: Session,
    *,
    league_slug: str = "",
    site_session: Session | None = None,
) -> int | None:
    """Draft ownership panels before the current in-game draft year are no longer active."""
    # Prefer in-game truth (live draft hub timeline year) when available.
    # Some historical leagues have season start/end year conventions that do not
    # match the actual entry draft year.
    if site_session is not None:
        slug = str(league_slug or "").strip()
        if slug:
            draft_year = site_session.scalar(
                select(LeagueDraft.timeline_year)
                .where(
                    LeagueDraft.league_slug == slug,
                    LeagueDraft.status != "completed",
                    LeagueDraft.timeline_year.isnot(None),
                )
                .order_by(LeagueDraft.timeline_year.asc(), LeagueDraft.id.asc())
                .limit(1)
            )
            if draft_year is not None:
                return int(draft_year)

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
    season = season_with_imported_data_fallback(league_session, season) or season
    if season is None:
        return None
    return _ownership_current_draft_year_for_season(season, league_slug=league_slug)


def complete_stale_draft_pick_ownership_panels(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    exclude_years: set[int] | None = None,
) -> int:
    """Mark panels from prior in-game draft years completed after the July 1 season rollover.

    ``exclude_years`` keeps explicitly reactivated past-year panels active (admin restore).
    """
    slug = str(league_slug or "").strip()
    if not slug:
        return 0
    cutoff_year = in_game_draft_ownership_cutoff_year(
        league_session,
        league_slug=slug,
        site_session=site_session,
    )
    if cutoff_year is None:
        return 0
    keep_years = {int(y) for y in (exclude_years or set())}
    panels = list(
        site_session.scalars(
            select(DraftPickOwnershipYear).where(
                DraftPickOwnershipYear.league_slug == slug,
                DraftPickOwnershipYear.status != "completed",
                DraftPickOwnershipYear.draft_year < int(cutoff_year),
            )
        ).all()
    )
    changed = 0
    for panel in panels:
        if (
            int(panel.draft_year) in keep_years
            or getattr(panel, "manual_status_override", False) is True
        ):
            continue
        panel.status = "completed"
        changed += 1
    return changed


def reactivate_current_draft_pick_ownership_panel_if_needed(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> bool:
    """Undo stale completion when a league's current draft year rule changes."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    current_year = in_game_draft_ownership_cutoff_year(
        league_session,
        league_slug=slug,
        site_session=site_session,
    )
    if current_year is None:
        return False
    panel = site_session.scalar(
        select(DraftPickOwnershipYear).where(
            DraftPickOwnershipYear.league_slug == slug,
            DraftPickOwnershipYear.draft_year == int(current_year),
        ).limit(1)
    )
    if (
        panel is None
        or str(panel.status or "active") != "completed"
        or getattr(panel, "manual_status_override", False) is True
    ):
        return False
    panel.status = "active"
    return True


def reset_calendar_seeded_panels_if_needed(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> bool:
    """Repair panels accidentally seeded from today's calendar for historical leagues."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    default_year = default_draft_pick_ownership_start_year(
        site_session, league_session, league_slug=slug
    )
    panels = list_draft_pick_ownership_year_panels(site_session, league_slug=slug)
    active = [p for p in panels if str(p.status or "active") != "completed"]
    if not active:
        return False
    if min(int(p.draft_year) for p in active) <= default_year + 20:
        return False
    years = [int(p.draft_year) for p in active]
    site_session.execute(
        delete(TradeMarketDraftPickOwnership).where(
            TradeMarketDraftPickOwnership.league_slug == slug,
            TradeMarketDraftPickOwnership.draft_year.in_(years),
        )
    )
    for panel in active:
        site_session.delete(panel)
    site_session.commit()
    return True


def _reorder_year_panels(site_session: Session, *, league_slug: str) -> None:
    rows = list_draft_pick_ownership_year_panels(site_session, league_slug=league_slug)
    for idx, row in enumerate(rows, start=1):
        row.display_order = int(idx)


def _team_fhm_by_db_id(league_session: Session) -> dict[int, int]:
    out: dict[int, int] = {}
    for team in league_session.scalars(select(Team)).all():
        raw = str(getattr(team, "fhm_team_id", None) or "").strip()
        if raw.isdigit():
            out[int(team.id)] = int(raw)
    return out


def _ensure_year_rows(
    site_session: Session,
    *,
    league_slug: str,
    draft_year: int,
    round_count: int,
    teams: list[Team],
) -> int:
    """Ensure one ownership row per team+round exists for a draft year."""
    slug = str(league_slug or "").strip()
    if not slug:
        return 0
    rounds = max(1, min(15, int(round_count)))
    deleted = site_session.execute(
        delete(TradeMarketDraftPickOwnership).where(
            TradeMarketDraftPickOwnership.league_slug == slug,
            TradeMarketDraftPickOwnership.draft_year == int(draft_year),
            TradeMarketDraftPickOwnership.round > rounds,
        )
    )
    existing = list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership).where(
                TradeMarketDraftPickOwnership.league_slug == slug,
                TradeMarketDraftPickOwnership.draft_year == int(draft_year),
            )
        ).all()
    )
    existing_by_key = {
        (int(r.original_team_fhm_id), int(r.round)): r
        for r in existing
    }
    created = 0
    for team in teams:
        raw = str(getattr(team, "fhm_team_id", None) or "").strip()
        if not raw.isdigit():
            continue
        fhm_id = int(raw)
        for rnd in range(1, rounds + 1):
            if (fhm_id, rnd) in existing_by_key:
                continue
            site_session.add(
                TradeMarketDraftPickOwnership(
                    league_slug=slug,
                    draft_year=int(draft_year),
                    original_team_fhm_id=fhm_id,
                    original_team_id=int(team.id),
                    round=int(rnd),
                    owner_team_fhm_id=fhm_id,
                    owner_team_id=int(team.id),
                )
            )
            created += 1
    return int((deleted.rowcount or 0) + created)


def ensure_draft_pick_ownership_panels(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    active_count: int = 3,
    default_round_count: int = 9,
    exclude_years: set[int] | None = None,
) -> list[DraftPickOwnershipYear]:
    """Guarantee the league has the configured number of active future-year panels."""
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    target_active = max(1, int(active_count))
    rounds = max(1, min(15, int(default_round_count)))
    keep_years = {int(y) for y in (exclude_years or set())}
    complete_stale_draft_pick_ownership_panels(
        site_session,
        league_session,
        league_slug=slug,
        exclude_years=keep_years,
    )
    reactivate_current_draft_pick_ownership_panel_if_needed(
        site_session,
        league_session,
        league_slug=slug,
    )
    # If the current in-game draft year panel is missing entirely (e.g. created under
    # an incorrect season-based mapping earlier), create it so the admin page can
    # show or restore traded ownership.
    current_year = in_game_draft_ownership_cutoff_year(
        league_session,
        league_slug=slug,
        site_session=site_session,
    )
    if current_year is not None:
        existing_panel = site_session.scalar(
            select(DraftPickOwnershipYear).where(
                DraftPickOwnershipYear.league_slug == slug,
                DraftPickOwnershipYear.draft_year == int(current_year),
            ).limit(1)
        )
        if existing_panel is None:
            site_session.add(
                DraftPickOwnershipYear(
                    league_slug=slug,
                    draft_year=int(current_year),
                    round_count=rounds,
                    status="active",
                    display_order=9999,
                )
            )
            site_session.flush()
    panels = list_draft_pick_ownership_year_panels(site_session, league_slug=slug)
    keep_years.update(
        int(panel.draft_year)
        for panel in panels
        if str(panel.status or "active") != "completed"
        and getattr(panel, "manual_status_override", False) is True
    )
    if panels:
        seed_start = max(int(p.draft_year) for p in panels) + 1
    else:
        latest_completed = site_session.scalar(
            select(DraftPickOwnershipYear.draft_year)
            .where(
                DraftPickOwnershipYear.league_slug == slug,
                DraftPickOwnershipYear.status == "completed",
            )
            .order_by(DraftPickOwnershipYear.draft_year.desc())
            .limit(1)
        )
        seed_start = (
            int(latest_completed) + 1
            if latest_completed is not None
            else default_draft_pick_ownership_start_year(
                site_session,
                league_session,
                league_slug=slug,
            )
        )
    active = [p for p in panels if str(p.status or "active") != "completed"]
    protected = [p for p in active if int(p.draft_year) in keep_years]
    window = [p for p in active if int(p.draft_year) not in keep_years]
    if len(window) > target_active:
        window_sorted = sorted(
            window,
            key=lambda p: (int(p.draft_year), int(p.display_order or 0), int(p.id or 0)),
        )
        keep_ids = {int(p.id) for p in window_sorted[:target_active] if p.id is not None}
        for panel in window_sorted[target_active:]:
            panel.status = "completed"
        window = [p for p in window_sorted if p.id is None or int(p.id) in keep_ids]
    teams = draft_pick_teams_for_grid(league_session)
    for panel in protected:
        _ensure_year_rows(
            site_session,
            league_slug=slug,
            draft_year=int(panel.draft_year),
            round_count=max(1, int(panel.round_count or rounds)),
            teams=teams,
        )
    while len(window) < target_active:
        next_year = _next_panel_year(site_session, league_slug=slug, fallback_start=seed_start)
        panel = DraftPickOwnershipYear(
            league_slug=slug,
            draft_year=int(next_year),
            round_count=rounds,
            status="active",
            display_order=9999,
        )
        site_session.add(panel)
        site_session.flush()
        _ensure_year_rows(
            site_session,
            league_slug=slug,
            draft_year=int(next_year),
            round_count=int(panel.round_count),
            teams=teams,
        )
        window.append(panel)
        panels.append(panel)
        seed_start = int(next_year) + 1
    _reorder_year_panels(site_session, league_slug=slug)
    site_session.commit()
    return list_draft_pick_ownership_year_panels(site_session, league_slug=slug)


def mark_completed_draft_year_and_roll_forward(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    draft_year: int,
    active_count: int = 3,
    default_round_count: int = 9,
) -> list[DraftPickOwnershipYear]:
    """Mark a draft-year panel completed, then top back up to the active panel target."""
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    panel = site_session.scalar(
        select(DraftPickOwnershipYear).where(
            DraftPickOwnershipYear.league_slug == slug,
            DraftPickOwnershipYear.draft_year == int(draft_year),
        ).limit(1)
    )
    if panel is not None:
        panel.status = "completed"
    panels = ensure_draft_pick_ownership_panels(
        site_session,
        league_session,
        league_slug=slug,
        active_count=active_count,
        default_round_count=default_round_count,
    )
    return panels


def build_draft_pick_ownership_year_grid(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    draft_year: int,
    round_count: int,
) -> list[dict[str, Any]]:
    """Grid rows for admin display/edit of one draft year."""
    slug = str(league_slug or "").strip()
    rounds = max(1, min(15, int(round_count)))
    teams = draft_pick_teams_for_grid(league_session)
    _ensure_year_rows(
        site_session,
        league_slug=slug,
        draft_year=int(draft_year),
        round_count=rounds,
        teams=teams,
    )
    site_session.commit()
    rows = list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership).where(
                TradeMarketDraftPickOwnership.league_slug == slug,
                TradeMarketDraftPickOwnership.draft_year == int(draft_year),
                TradeMarketDraftPickOwnership.round <= rounds,
            )
        ).all()
    )
    by_key = {(int(r.original_team_fhm_id), int(r.round)): r for r in rows}
    out: list[dict[str, Any]] = []
    for team in teams:
        raw = str(getattr(team, "fhm_team_id", None) or "").strip()
        if not raw.isdigit():
            continue
        fhm_id = int(raw)
        cells: list[dict[str, Any]] = []
        for rnd in range(1, rounds + 1):
            row = by_key.get((fhm_id, rnd))
            cells.append(
                {
                    "round": int(rnd),
                    "row_id": int(row.id) if row else None,
                    "owner_team_id": int(row.owner_team_id) if row and row.owner_team_id else None,
                    "owner_team_fhm_id": int(row.owner_team_fhm_id) if row else None,
                }
            )
        out.append(
            {
                "team_id": int(team.id),
                "team_fhm_id": int(fhm_id),
                "abbr": str(team.abbreviation or "").strip() or f"T{fhm_id}",
                "name": team.full_display_name(),
                "team": team,
                "cells": cells,
            }
        )
    return out


def save_draft_pick_ownership_year_grid(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    draft_year: int,
    round_count: int,
    owner_by_key: dict[tuple[int, int], int],
) -> int:
    """Persist ownership map for one year; key is (original_team_fhm_id, round)."""
    slug = str(league_slug or "").strip()
    rounds = max(1, min(15, int(round_count)))
    teams = draft_pick_teams_for_grid(league_session)
    _ensure_year_rows(
        site_session,
        league_slug=slug,
        draft_year=int(draft_year),
        round_count=rounds,
        teams=teams,
    )
    panel = site_session.scalar(
        select(DraftPickOwnershipYear).where(
            DraftPickOwnershipYear.league_slug == slug,
            DraftPickOwnershipYear.draft_year == int(draft_year),
        ).limit(1)
    )
    if panel is None:
        panel = DraftPickOwnershipYear(
            league_slug=slug,
            draft_year=int(draft_year),
            round_count=rounds,
            status="active",
            display_order=9999,
        )
        site_session.add(panel)
        site_session.flush()
    panel.round_count = rounds
    db_to_fhm = _team_fhm_by_db_id(league_session)
    rows = list(
        site_session.scalars(
            select(TradeMarketDraftPickOwnership).where(
                TradeMarketDraftPickOwnership.league_slug == slug,
                TradeMarketDraftPickOwnership.draft_year == int(draft_year),
                TradeMarketDraftPickOwnership.round <= rounds,
            )
        ).all()
    )
    updated = 0
    for row in rows:
        key = (int(row.original_team_fhm_id), int(row.round))
        owner_team_id = owner_by_key.get(key)
        if owner_team_id is None:
            continue
        if int(row.owner_team_id or -1) == int(owner_team_id):
            continue
        row.owner_team_id = int(owner_team_id)
        row.owner_team_fhm_id = int(
            db_to_fhm.get(int(owner_team_id), int(row.owner_team_fhm_id or row.original_team_fhm_id))
        )
        updated += 1
    _reorder_year_panels(site_session, league_slug=slug)
    site_session.commit()
    return updated


def transfer_approved_trade_draft_pick_rows(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    from_team_id: int,
    to_team_id: int,
    left_out: list[str],
    right_out: list[str],
) -> list[dict[str, int]]:
    """Move ownership rows for approved trade ledger draft-pick assets."""
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    db_to_fhm = _team_fhm_by_db_id(league_session)
    moves: list[tuple[str, int]] = []
    for key in left_out:
        rid = parse_draft_pick_drag_key(key)
        if rid is not None:
            moves.append(("to", int(rid)))
    for key in right_out:
        rid = parse_draft_pick_drag_key(key)
        if rid is not None:
            moves.append(("from", int(rid)))
    changed: list[dict[str, int]] = []
    seen_ids: set[int] = set()
    for direction, rid in moves:
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        row = site_session.get(TradeMarketDraftPickOwnership, int(rid))
        if row is None or str(row.league_slug or "") != slug:
            continue
        target_team_id = int(to_team_id if direction == "to" else from_team_id)
        if int(row.owner_team_id or -1) == target_team_id:
            continue
        prev_owner_id = int(row.owner_team_id or -1)
        row.owner_team_id = target_team_id
        row.owner_team_fhm_id = int(
            db_to_fhm.get(target_team_id, int(row.owner_team_fhm_id or row.original_team_fhm_id))
        )
        changed.append(
            {
                "row_id": int(row.id),
                "draft_year": int(row.draft_year),
                "round": int(row.round),
                "original_team_fhm_id": int(row.original_team_fhm_id),
                "previous_owner_team_id": prev_owner_id,
                "new_owner_team_id": target_team_id,
            }
        )
    return changed


def sync_draft_pick_ownership_rollover_for_completed_drafts(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    active_count: int = 3,
    default_round_count: int = 9,
) -> list[DraftPickOwnershipYear]:
    """Mark any completed draft years as completed panels, then top up active panel count."""
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    complete_stale_draft_pick_ownership_panels(
        site_session,
        league_session,
        league_slug=slug,
    )
    reactivate_current_draft_pick_ownership_panel_if_needed(
        site_session,
        league_session,
        league_slug=slug,
    )
    completed_years = {
        int(y)
        for y in site_session.scalars(
            select(LeagueDraft.timeline_year).where(
                LeagueDraft.league_slug == slug,
                LeagueDraft.status == "completed",
                LeagueDraft.timeline_year.isnot(None),
            )
        ).all()
        if y is not None
    }
    if completed_years:
        for panel in site_session.scalars(
            select(DraftPickOwnershipYear).where(
                DraftPickOwnershipYear.league_slug == slug,
                DraftPickOwnershipYear.draft_year.in_(sorted(completed_years)),
                DraftPickOwnershipYear.status != "completed",
            )
        ).all():
            panel.status = "completed"
    return ensure_draft_pick_ownership_panels(
        site_session,
        league_session,
        league_slug=slug,
        active_count=active_count,
        default_round_count=default_round_count,
    )
