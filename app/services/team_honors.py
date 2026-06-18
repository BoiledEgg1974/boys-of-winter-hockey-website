"""Team page honors: retired numbers and victory banners."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TeamHonorsMeta, TeamRetiredNumber, TeamVictoryBanner


def _retired_sort_key(row: TeamRetiredNumber) -> tuple[int, int, int]:
    return (int(row.sort_order or 0), int(row.jersey_number), int(row.id))


def _banner_sort_key(row: TeamVictoryBanner) -> tuple[int, int, int]:
    order = int(row.sort_order or 0)
    if order != 0:
        return (order, int(row.victory_number), int(row.id))
    return (0, int(row.victory_number), int(row.id))


def get_team_honors_meta(session: Session, team_id: int) -> TeamHonorsMeta | None:
    return session.get(TeamHonorsMeta, int(team_id))


def ensure_team_honors_meta(session: Session, team_id: int) -> TeamHonorsMeta:
    row = session.get(TeamHonorsMeta, int(team_id))
    if row is None:
        row = TeamHonorsMeta(team_id=int(team_id), retired_section_enabled=False)
        session.add(row)
    return row


def active_retired_numbers_for_team(session: Session, team_id: int) -> list[TeamRetiredNumber]:
    rows = list(
        session.scalars(
            select(TeamRetiredNumber).where(
                TeamRetiredNumber.team_id == int(team_id),
                TeamRetiredNumber.is_active.is_(True),
            )
        ).all()
    )
    rows.sort(key=_retired_sort_key)
    return rows


def active_victory_banners_for_team(session: Session, team_id: int) -> list[TeamVictoryBanner]:
    rows = list(
        session.scalars(
            select(TeamVictoryBanner).where(
                TeamVictoryBanner.team_id == int(team_id),
                TeamVictoryBanner.is_active.is_(True),
            )
        ).all()
    )
    rows.sort(key=_banner_sort_key)
    return rows


def honors_panels_should_stack(*, retired_count: int, banner_count: int) -> bool:
    """Stack honors panels vertically when a row cannot keep original item sizing."""
    if retired_count > 8:
        return True
    if banner_count > 6:
        return True
    return False


def team_honors_page_bundle(session: Session, team_id: int) -> dict[str, object]:
    """Display bundle for team.html honors section."""
    meta = get_team_honors_meta(session, team_id)
    retired_enabled = bool(meta and meta.retired_section_enabled)
    retired_rows = active_retired_numbers_for_team(session, team_id)
    banner_rows = active_victory_banners_for_team(session, team_id)
    banner_count = sum(1 for row in banner_rows if row.banner_image_rel_path)
    show_retired_panel = retired_enabled or bool(retired_rows)
    show_banner_panel = bool(banner_rows)
    show_honors_section = show_retired_panel or show_banner_panel
    stack_panels = honors_panels_should_stack(
        retired_count=len(retired_rows),
        banner_count=banner_count,
    )
    split_panels = show_retired_panel and show_banner_panel and not stack_panels
    return {
        "team_honors_show_section": show_honors_section,
        "team_honors_show_retired_panel": show_retired_panel,
        "team_honors_show_banner_panel": show_banner_panel,
        "team_honors_panels_split": split_panels,
        "team_honors_panels_stack": stack_panels,
        "team_honors_retired_enabled": retired_enabled,
        "team_honors_retired_rows": retired_rows,
        "team_honors_banner_rows": banner_rows,
        "team_honors_banner_count": banner_count,
    }
