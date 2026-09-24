"""Front Office dashboard rows (transfer room, cap, draft picks) for BOWL-Relegation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.draft_pick_ownership import draft_pick_asset_dicts
from app.services.league_finances import build_league_finances_context
from app.services.staff_salaries import main_league_teams
from app.services.transfer_rules import bowl_team_budget_snapshot


def build_front_office_rows(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    raw_import_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    slug = str(league_slug or "").strip()
    fin = build_league_finances_context(
        league_session, league_slug=slug, raw_import_dir=raw_import_dir
    )
    meta = {
        "season_label": fin.get("season_label"),
        "cap_ceiling": fin.get("cap_ceiling"),
    }
    cap_ceiling = fin.get("cap_ceiling")
    finance_by_team_id = {
        int(row["team"].id): row for row in fin.get("teams") or [] if row.get("team") is not None
    }

    teams = sorted(main_league_teams(league_session), key=lambda t: t.full_display_name().lower())
    out: list[dict[str, Any]] = []
    for team in teams:
        tid = int(team.id)
        fin_row = finance_by_team_id.get(tid, {})
        transfer = bowl_team_budget_snapshot(
            league_session, bowl_team_id=tid, league_slug=slug
        )
        picks = draft_pick_asset_dicts(
            site_session,
            league_session,
            league_slug=slug,
            team_id=tid,
        )
        pick_labels = [str(p.get("label") or "").strip() for p in picks if p.get("label")]
        out.append(
            {
                "team": team,
                "cap_ceiling": cap_ceiling,
                "cap_hit": fin_row.get("current_cap"),
                "cap_space": fin_row.get("cap_space"),
                "cap_room_usd": transfer.get("cap_room_usd"),
                "transfer_override_usd": transfer.get("transfer_cash_override_usd"),
                "transfer_effective_usd": transfer.get("remaining_budget_usd"),
                "draft_pick_labels": pick_labels,
            }
        )
    return out, meta
