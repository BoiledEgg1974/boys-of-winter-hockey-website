"""BLUP / BLOW / combined scoping for league record boards and Discord snapshots."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Team
from app.services.relegation import (
    RelegationScope,
    RelegationTierConfig,
    get_tier_config,
    is_relegation_league,
    scope_league_ids_for_records,
    team_fhm_ids_for_scope,
    team_ids_for_scope,
    team_tier,
)

RECORD_REL_SCOPES: tuple[RelegationScope, ...] = ("combined", "upper", "lower")

RELEGATION_TIER_DISCORD_EMOTES: dict[str, str] = {
    "upper": "<:BLUP:1542322220746145812>",
    "lower": "<:BLOW:1542322233702097086>",
}


def record_rel_scopes_for_league(league_slug: str) -> tuple[RelegationScope, ...]:
    if is_relegation_league(league_slug):
        return RECORD_REL_SCOPES
    return ("combined",)


def record_snapshot_scope_prefix(rel_scope: RelegationScope) -> str:
    scope = str(rel_scope or "combined").strip().lower()
    if scope not in RECORD_REL_SCOPES:
        scope = "combined"
    return f"rec:{scope}:"


def normalize_record_snapshot_key(snapshot_key: str, *, league_slug: str) -> str:
    """Map legacy unprefixed keys to combined scope on BOWL-Relegation."""
    key = str(snapshot_key or "").strip()
    if not key or not is_relegation_league(league_slug):
        return key
    if key.startswith("rec:"):
        return key
    return f"{record_snapshot_scope_prefix('combined')}{key}"


def scope_filters(
    session: Session,
    rel_scope: RelegationScope,
    config: RelegationTierConfig,
) -> dict[str, object]:
    """Filters for all-time / season / team / game record queries."""
    return {
        "league_ids": scope_league_ids_for_records(session, rel_scope, config),
        "team_fhm_ids": team_fhm_ids_for_scope(session, rel_scope, config),
        "team_ids": team_ids_for_scope(session, rel_scope, config),
    }


def team_ids_for_rel_scope(
    session: Session,
    rel_scope: RelegationScope,
    config: RelegationTierConfig,
) -> frozenset[int] | None:
    """Site team ids included in this record scope (None = all main-tier clubs)."""
    if rel_scope == "combined":
        from app.services.relegation import relegation_main_tier_team_ids

        ids = relegation_main_tier_team_ids(session)
        return ids if ids else None
    ids = team_ids_for_scope(session, rel_scope, config)
    return ids if ids else frozenset()


def relegation_tier_for_team(
    team: Team | None,
    config: RelegationTierConfig | None,
) -> str | None:
    if team is None or config is None:
        return None
    tier = team_tier(team, config)
    return tier if tier in ("upper", "lower") else None


def relegation_tier_emote_for_team(
    team: Team | None,
    config: RelegationTierConfig | None,
) -> str:
    tier = relegation_tier_for_team(team, config)
    if tier:
        return RELEGATION_TIER_DISCORD_EMOTES.get(tier, "")
    return ""


def relegation_tier_emote_for_scope(rel_scope: RelegationScope) -> str:
    if rel_scope == "upper":
        return RELEGATION_TIER_DISCORD_EMOTES["upper"]
    if rel_scope == "lower":
        return RELEGATION_TIER_DISCORD_EMOTES["lower"]
    return ""


def tier_config_for_session(session: Session) -> RelegationTierConfig | None:
    from flask import current_app

    slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    if not is_relegation_league(slug):
        return None
    return get_tier_config(session)
