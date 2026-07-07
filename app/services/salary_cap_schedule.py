"""Per-season salary cap ceiling/floor panels (RFA + finances)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Season
from app.services.league_rules import rule_int
from app.services.seasons import get_current_season
from app.site_models import LeagueSalaryCapYear


def season_label_from_start_year(start_year: int) -> str:
    y = int(start_year)
    return f"{y}-{(y + 1) % 100:02d}"


def _current_season_start_year(league_session: Session) -> int | None:
    season = get_current_season()
    if season is None:
        season = league_session.scalar(
            select(Season)
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
            .limit(1)
        )
    if season is None or season.start_year is None:
        return None
    return int(season.start_year)


def list_salary_cap_year_panels(
    site_session: Session,
    *,
    league_slug: str,
) -> list[LeagueSalaryCapYear]:
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    return list(
        site_session.scalars(
            select(LeagueSalaryCapYear)
            .where(LeagueSalaryCapYear.league_slug == slug)
            .order_by(
                LeagueSalaryCapYear.display_order.asc(),
                LeagueSalaryCapYear.season_start_year.asc(),
                LeagueSalaryCapYear.id.asc(),
            )
        ).all()
    )


def _league_rules_cap_defaults(site_session: Session, league_slug: str) -> tuple[int | None, int | None]:
    ceiling_raw = rule_int(site_session, league_slug, "salary_cap_amount", default=0)
    floor_raw = rule_int(site_session, league_slug, "salary_cap_floor", default=0)
    ceiling = int(ceiling_raw) if ceiling_raw > 0 else None
    floor = int(floor_raw) if floor_raw > 0 else None
    return ceiling, floor


def cap_for_season(
    site_session: Session,
    league_slug: str,
    season_start_year: int,
    *,
    fallback_to_rules: bool = True,
) -> tuple[int | None, int | None]:
    slug = str(league_slug or "").strip()
    if not slug:
        return None, None
    panel = site_session.scalar(
        select(LeagueSalaryCapYear).where(
            LeagueSalaryCapYear.league_slug == slug,
            LeagueSalaryCapYear.season_start_year == int(season_start_year),
        ).limit(1)
    )
    ceiling = int(panel.cap_ceiling) if panel is not None and panel.cap_ceiling is not None else None
    floor = int(panel.cap_floor) if panel is not None and panel.cap_floor is not None else None
    if fallback_to_rules:
        current = _current_season_start_year(site_session)
        if current is not None and int(season_start_year) == int(current):
            rules_ceiling, rules_floor = _league_rules_cap_defaults(site_session, slug)
            if ceiling is None:
                ceiling = rules_ceiling
            if floor is None:
                floor = rules_floor
    return ceiling, floor


def complete_stale_salary_cap_panels(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
) -> int:
    """Mark cap panels from prior seasons completed after July 1 rollover."""
    slug = str(league_slug or "").strip()
    current = _current_season_start_year(league_session)
    if not slug or current is None:
        return 0
    panels = list(
        site_session.scalars(
            select(LeagueSalaryCapYear).where(
                LeagueSalaryCapYear.league_slug == slug,
                LeagueSalaryCapYear.status != "completed",
                LeagueSalaryCapYear.season_start_year < int(current),
            )
        ).all()
    )
    for panel in panels:
        panel.status = "completed"
    return len(panels)


def _reorder_cap_panels(site_session: Session, *, league_slug: str) -> None:
    rows = list_salary_cap_year_panels(site_session, league_slug=league_slug)
    active = [r for r in rows if str(r.status or "active") != "completed"]
    for idx, row in enumerate(active, start=1):
        row.display_order = int(idx)


def _get_or_create_panel(
    site_session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    seed_ceiling: int | None = None,
    seed_floor: int | None = None,
) -> LeagueSalaryCapYear:
    panel = site_session.scalar(
        select(LeagueSalaryCapYear).where(
            LeagueSalaryCapYear.league_slug == league_slug,
            LeagueSalaryCapYear.season_start_year == int(season_start_year),
        ).limit(1)
    )
    if panel is None:
        panel = LeagueSalaryCapYear(
            league_slug=league_slug,
            season_start_year=int(season_start_year),
            cap_ceiling=seed_ceiling,
            cap_floor=seed_floor,
            status="active",
            display_order=9999,
        )
        site_session.add(panel)
        site_session.flush()
    else:
        panel.status = "active"
    return panel


def ensure_salary_cap_panels(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    active_count: int = 3,
) -> list[LeagueSalaryCapYear]:
    """Guarantee active cap panels for current season through two future seasons."""
    slug = str(league_slug or "").strip()
    if not slug:
        return []
    current = _current_season_start_year(league_session)
    if current is None:
        return list_salary_cap_year_panels(site_session, league_slug=slug)

    complete_stale_salary_cap_panels(site_session, league_session, league_slug=slug)
    target_years = [int(current) + i for i in range(max(1, int(active_count)))]
    rules_ceiling, rules_floor = _league_rules_cap_defaults(site_session, slug)

    for year in target_years:
        seed_ceiling = rules_ceiling if int(year) == int(current) else None
        seed_floor = rules_floor if int(year) == int(current) else None
        existing = site_session.scalar(
            select(LeagueSalaryCapYear).where(
                LeagueSalaryCapYear.league_slug == slug,
                LeagueSalaryCapYear.season_start_year == int(year),
            ).limit(1)
        )
        if existing is None:
            _get_or_create_panel(
                site_session,
                league_slug=slug,
                season_start_year=int(year),
                seed_ceiling=seed_ceiling,
                seed_floor=seed_floor,
            )
        elif str(existing.status or "active") != "completed":
            existing.status = "active"
            if int(year) == int(current):
                if existing.cap_ceiling is None and seed_ceiling is not None:
                    existing.cap_ceiling = seed_ceiling
                if existing.cap_floor is None and seed_floor is not None:
                    existing.cap_floor = seed_floor

    extra_active = list(
        site_session.scalars(
            select(LeagueSalaryCapYear).where(
                LeagueSalaryCapYear.league_slug == slug,
                LeagueSalaryCapYear.status != "completed",
                LeagueSalaryCapYear.season_start_year.not_in(target_years),
            )
        ).all()
    )
    for panel in extra_active:
        panel.status = "completed"

    _reorder_cap_panels(site_session, league_slug=slug)
    site_session.commit()
    return list_salary_cap_year_panels(site_session, league_slug=slug)


def sync_salary_cap_schedule_rollover(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    active_count: int = 3,
) -> list[LeagueSalaryCapYear]:
    """Complete stale cap panels and top up to the active season window."""
    return ensure_salary_cap_panels(
        site_session,
        league_session,
        league_slug=league_slug,
        active_count=active_count,
    )


def save_salary_cap_panel(
    site_session: Session,
    *,
    league_slug: str,
    panel_id: int,
    cap_ceiling: int | None,
    cap_floor: int | None,
) -> LeagueSalaryCapYear | None:
    slug = str(league_slug or "").strip()
    panel = site_session.get(LeagueSalaryCapYear, int(panel_id))
    if panel is None or panel.league_slug != slug:
        return None
    if cap_ceiling is not None and cap_ceiling < 0:
        cap_ceiling = None
    if cap_floor is not None and cap_floor < 0:
        cap_floor = None
    panel.cap_ceiling = int(cap_ceiling) if cap_ceiling is not None else None
    panel.cap_floor = int(cap_floor) if cap_floor is not None else None
    panel.status = "active"
    site_session.commit()
    return panel


def build_cap_panels_view(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    active_count: int = 3,
) -> list[dict[str, object]]:
    panels = ensure_salary_cap_panels(
        site_session,
        league_session,
        league_slug=league_slug,
        active_count=active_count,
    )
    current = _current_season_start_year(league_session)
    active = [p for p in panels if str(p.status or "active") != "completed"]
    if current is not None:
        active = sorted(
            [p for p in active if int(p.season_start_year) >= int(current)],
            key=lambda p: (int(p.season_start_year), int(p.display_order or 0), int(p.id or 0)),
        )[:active_count]
    out: list[dict[str, object]] = []
    for panel in active:
        out.append(
            {
                "panel": panel,
                "season_label": season_label_from_start_year(int(panel.season_start_year)),
                "cap_ceiling": panel.cap_ceiling,
                "cap_floor": panel.cap_floor,
            }
        )
    return out
