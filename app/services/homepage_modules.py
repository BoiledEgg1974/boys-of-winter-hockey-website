from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.site_models import HomepageModuleSetting

ALLOWED_HOMEPAGE_MODULE_KEYS = (
    "schedule",
    "postseason_odds",
    "game_of_the_night",
    "next_game_to_watch",
    "three_stars",
    "milestones_watch",
    "special_teams_snapshot",
    "around_the_league",
    "league_leaders",
    "top_rookies",
    "player_momentum",
    "team_momentum",
    "league_spotlight",
    "divisional_standings",
    "power_rankings",
    "identity_panel",
    "champions",
)

DEFAULT_HOMEPAGE_MODULES = (
    {"module_key": "schedule", "sort_order": 10},
    {"module_key": "postseason_odds", "sort_order": 20},
    {"module_key": "game_of_the_night", "sort_order": 30},
    {"module_key": "next_game_to_watch", "sort_order": 40},
    {"module_key": "three_stars", "sort_order": 50},
    {"module_key": "milestones_watch", "sort_order": 60},
    {"module_key": "special_teams_snapshot", "sort_order": 70},
    {"module_key": "around_the_league", "sort_order": 80},
    {"module_key": "league_leaders", "sort_order": 90},
    {"module_key": "top_rookies", "sort_order": 100},
    {"module_key": "player_momentum", "sort_order": 110},
    {"module_key": "team_momentum", "sort_order": 120},
    {"module_key": "league_spotlight", "sort_order": 130},
    {"module_key": "divisional_standings", "sort_order": 140},
    {"module_key": "power_rankings", "sort_order": 150},
    {"module_key": "identity_panel", "sort_order": 160},
    {"module_key": "champions", "sort_order": 170},
)

DEFAULT_VISIBILITY = {r["module_key"]: True for r in DEFAULT_HOMEPAGE_MODULES}
DEFAULT_SORT_ORDER = {r["module_key"]: int(r["sort_order"]) for r in DEFAULT_HOMEPAGE_MODULES}


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value or "").strip().lower()
    return s in {"1", "true", "yes", "on"}


def ensure_homepage_module_settings(session, league_slug: str, updated_by_user_id: int | None = None) -> None:
    existing = session.scalars(
        select(HomepageModuleSetting).where(HomepageModuleSetting.league_slug == league_slug)
    ).all()
    by_key = {r.module_key: r for r in existing}
    now = datetime.utcnow()
    changed = False
    for row in DEFAULT_HOMEPAGE_MODULES:
        key = str(row["module_key"])
        if key in by_key:
            continue
        session.add(
            HomepageModuleSetting(
                league_slug=league_slug,
                module_key=key,
                is_enabled=True,
                sort_order=int(row["sort_order"]),
                updated_by_user_id=updated_by_user_id,
                updated_at=now,
            )
        )
        changed = True
    if changed:
        session.commit()


def get_homepage_module_settings(session, league_slug: str) -> list[HomepageModuleSetting]:
    ensure_homepage_module_settings(session, league_slug)
    return session.scalars(
        select(HomepageModuleSetting)
        .where(HomepageModuleSetting.league_slug == league_slug)
        .order_by(HomepageModuleSetting.sort_order.asc(), HomepageModuleSetting.id.asc())
    ).all()


def module_visibility_map(session, league_slug: str) -> dict[str, bool]:
    rows = get_homepage_module_settings(session, league_slug)
    out = dict(DEFAULT_VISIBILITY)
    for r in rows:
        if r.module_key in DEFAULT_VISIBILITY:
            out[r.module_key] = bool(r.is_enabled)
    return out


def module_sort_order_map(session, league_slug: str) -> dict[str, int]:
    rows = get_homepage_module_settings(session, league_slug)
    out = dict(DEFAULT_SORT_ORDER)
    for r in rows:
        if r.module_key in DEFAULT_SORT_ORDER:
            out[r.module_key] = int(r.sort_order)
    return out


def save_homepage_module_settings(
    session,
    league_slug: str,
    rows: list[dict],
    updated_by_user_id: int,
) -> list[dict]:
    ensure_homepage_module_settings(session, league_slug, updated_by_user_id=updated_by_user_id)
    existing = session.scalars(
        select(HomepageModuleSetting).where(HomepageModuleSetting.league_slug == league_slug)
    ).all()
    by_key = {r.module_key: r for r in existing}
    now = datetime.utcnow()
    for item in rows:
        key = str(item.get("module_key") or "").strip()
        if key not in ALLOWED_HOMEPAGE_MODULE_KEYS:
            continue
        row = by_key.get(key)
        if row is None:
            row = HomepageModuleSetting(
                league_slug=league_slug,
                module_key=key,
                is_enabled=True,
                sort_order=DEFAULT_SORT_ORDER.get(key, 999),
            )
            session.add(row)
            by_key[key] = row
        try:
            sort_order = int(item.get("sort_order"))
        except (TypeError, ValueError):
            sort_order = DEFAULT_SORT_ORDER.get(key, 999)
        sort_order = max(0, min(10000, sort_order))
        row.is_enabled = _normalize_bool(item.get("is_enabled"))
        row.sort_order = sort_order
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = now
    session.commit()
    out_rows = get_homepage_module_settings(session, league_slug)
    return [
        {
            "module_key": r.module_key,
            "is_enabled": bool(r.is_enabled),
            "sort_order": int(r.sort_order),
        }
        for r in out_rows
    ]
