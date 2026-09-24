"""Filter homepage dashboard payloads by BOWL-Relegation scope (BLUP / BLOW / combined)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.models import Team
from app.services.relegation import (
    RelegationScope,
    filter_teams_to_main_tiers,
    get_tier_config,
    normalize_relegation_scope,
    relegation_features_enabled,
    team_ids_for_scope,
)


def resolve_homepage_relegation_scope(
    session: Session,
    *,
    league_slug: str,
    raw_scope: str | None,
    raw_import_dir,
) -> tuple[RelegationScope, frozenset[int] | None]:
    """Return (scope, team_ids) where team_ids is set when upper/lower filtering applies."""
    scope: RelegationScope = normalize_relegation_scope(raw_scope)
    if league_slug != "bowl-fantasy" or not relegation_features_enabled(league_slug):
        return "combined", None
    cfg = get_tier_config(session, raw_import_dir=raw_import_dir)
    if scope == "combined":
        main = filter_teams_to_main_tiers(
            list(session.scalars(select(Team)).all()),
            cfg,
        )
        return scope, frozenset(int(t.id) for t in main)
    ids = team_ids_for_scope(session, scope, cfg)
    return scope, frozenset(int(x) for x in ids) if ids else frozenset()


def game_both_teams_in_scope(
    home_team_id: int | None,
    away_team_id: int | None,
    allowed_team_ids: frozenset[int] | None,
) -> bool:
    if allowed_team_ids is None:
        return True
    if home_team_id is None or away_team_id is None:
        return False
    return int(home_team_id) in allowed_team_ids and int(away_team_id) in allowed_team_ids


def _standing_row_sort_key(row: dict[str, Any]) -> tuple:
    return (
        -int(row.get("pts") or 0),
        -int(row.get("w") or 0),
        str(row.get("name") or row.get("abbr") or ""),
    )


def filter_standings_by_division_payload(
    divisions: list[dict[str, Any]],
    allowed_team_ids: frozenset[int] | None,
    *,
    relegation_scope: RelegationScope = "combined",
) -> list[dict[str, Any]]:
    if allowed_team_ids is None:
        return divisions

    if relegation_scope in ("upper", "lower"):
        kept: list[dict[str, Any]] = []
        for div in divisions:
            for row in div.get("teams") or div.get("rows") or []:
                if int(row.get("team_id") or 0) in allowed_team_ids:
                    kept.append(dict(row))
        kept.sort(key=_standing_row_sort_key)
        for i, row in enumerate(kept, start=1):
            row["rank"] = i
        return [{"division": "League", "teams": kept}] if kept else []

    out: list[dict[str, Any]] = []
    for div in divisions:
        rows = div.get("teams") or div.get("rows") or []
        kept = [dict(r) for r in rows if int(r.get("team_id") or 0) in allowed_team_ids]
        if not kept:
            continue
        kept.sort(key=_standing_row_sort_key)
        for i, row in enumerate(kept, start=1):
            row["rank"] = i
        block = dict(div)
        if "teams" in div:
            block["teams"] = kept
        else:
            block["rows"] = kept
        out.append(block)
    return out


def filter_power_rankings_payload(
    payload: dict[str, list[dict[str, Any]]],
    allowed_team_ids: frozenset[int] | None,
) -> dict[str, list[dict[str, Any]]]:
    if allowed_team_ids is None:
        return payload

    def _keep(row: dict[str, Any]) -> bool:
        tid = row.get("team_id")
        if tid is None:
            slug = row.get("team_slug")
            return bool(slug)
        return int(tid) in allowed_team_ids

    teams = [r for r in payload.get("teams") or [] if _keep(r)]
    top5 = [r for r in payload.get("top5") or [] if _keep(r)]
    bottom5 = [r for r in payload.get("bottom5") or [] if _keep(r)]
    return {"teams": teams, "top5": top5, "bottom5": bottom5}


def leaders_fhm_league_ids_for_scope(
    session: Session,
    scope: RelegationScope,
    *,
    league_slug: str,
    raw_import_dir,
) -> tuple[int, ...] | None:
    """Narrow FHM league id filter for homepage leaders on upper/lower tabs."""
    if league_slug != "bowl-fantasy" or not relegation_features_enabled(league_slug):
        return None
    cfg = get_tier_config(session, raw_import_dir=raw_import_dir)
    if scope == "combined" and cfg.upper_league_ids and cfg.lower_league_ids:
        return tuple(sorted(int(x) for x in (cfg.upper_league_ids | cfg.lower_league_ids)))
    if scope == "upper" and cfg.upper_league_ids:
        return tuple(sorted(int(x) for x in cfg.upper_league_ids))
    if scope == "lower" and cfg.lower_league_ids:
        return tuple(sorted(int(x) for x in cfg.lower_league_ids))
    return None


def team_slugs_for_scope(
    session: Session,
    allowed_team_ids: frozenset[int] | None,
) -> frozenset[str] | None:
    if allowed_team_ids is None:
        return None
    if not allowed_team_ids:
        return frozenset()
    slugs = session.scalars(select(Team.slug).where(Team.id.in_(allowed_team_ids))).all()
    return frozenset(str(s) for s in slugs if s)


def filter_rows_by_team_slug(
    rows: list[dict[str, Any]],
    allowed_slugs: frozenset[str] | None,
    *,
    slug_key: str = "team_slug",
) -> list[dict[str, Any]]:
    if allowed_slugs is None:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        slug = (row.get(slug_key) or "").strip()
        if slug and slug in allowed_slugs:
            out.append(row)
    return out


def filter_leaders_payload(
    leaders: dict[str, list[dict[str, Any]]],
    allowed_slugs: frozenset[str] | None,
) -> dict[str, list[dict[str, Any]]]:
    if allowed_slugs is None:
        return leaders
    return {key: filter_rows_by_team_slug(rows, allowed_slugs) for key, rows in leaders.items()}


def filter_trending_players_payload(
    payload: dict[str, list[dict[str, Any]]],
    allowed_slugs: frozenset[str] | None,
) -> dict[str, list[dict[str, Any]]]:
    if allowed_slugs is None:
        return payload
    return {
        "hot": filter_rows_by_team_slug(payload.get("hot") or [], allowed_slugs),
        "cold": filter_rows_by_team_slug(payload.get("cold") or [], allowed_slugs),
    }


def filter_team_momentum_payload(
    payload: dict[str, Any],
    allowed_slugs: frozenset[str] | None,
) -> dict[str, Any]:
    if allowed_slugs is None:
        return payload
    trending = payload.get("trending") or {}
    streaks = payload.get("streaks") or {}
    return {
        "trending": {
            "hot": filter_rows_by_team_slug(trending.get("hot") or [], allowed_slugs),
            "cold": filter_rows_by_team_slug(trending.get("cold") or [], allowed_slugs),
        },
        "streaks": {
            key: filter_rows_by_team_slug(streaks.get(key) or [], allowed_slugs)
            for key in streaks
        },
    }


def game_spotlight_in_scope(
    card: dict[str, Any] | None,
    allowed_slugs: frozenset[str] | None,
) -> bool:
    if card is None or allowed_slugs is None:
        return True
    hs = (card.get("home_slug") or "").strip()
    aw = (card.get("away_slug") or "").strip()
    if not hs or not aw:
        return True
    return hs in allowed_slugs and aw in allowed_slugs
