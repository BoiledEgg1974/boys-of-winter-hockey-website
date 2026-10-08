"""League-wide and BLUP/BLOW tier broadcast tags for admin news."""
from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team
from app.services.draft_hub_state import gm_user_ids_for_team
from app.services.gm_messaging import gm_discord_name
from app.services.relegation import (
    RelegationTier,
    filter_teams_by_scope,
    get_tier_config,
    is_relegation_league,
    relegation_features_enabled,
)
from app.site_models import NewsArticle, User

NewsBroadcastScope = Literal["league", "upper", "lower"]

ADMIN_NEWS_SELECT_LEAGUE = "league"
ADMIN_NEWS_SELECT_TIER_UPPER = "tier-upper"
ADMIN_NEWS_SELECT_TIER_LOWER = "tier-lower"

_TIER_DISPLAY_LABELS: dict[NewsBroadcastScope, str] = {
    "upper": "BLUP League",
    "lower": "BLOW League",
}


def ensure_news_articles_broadcast_scope_column(app) -> None:
    """Add ``broadcast_scope`` for tier-wide admin headlines (BLUP / BLOW)."""
    from sqlalchemy import inspect, text

    from app.league_db import db

    engine = db.get_engine(app, bind="site")
    with engine.begin() as conn:
        colnames = {col["name"] for col in inspect(conn).get_columns("news_articles")}
        if "broadcast_scope" not in colnames:
            conn.execute(
                text("ALTER TABLE news_articles ADD COLUMN broadcast_scope VARCHAR(16)")
            )


def admin_news_tier_broadcast_enabled(league_slug: str) -> bool:
    slug = (league_slug or "").strip()
    return is_relegation_league(slug) and relegation_features_enabled(slug)


def admin_news_broadcast_choices(league_slug: str) -> list[tuple[str, str]]:
    """Extra team-select options after ``League`` (value, label)."""
    if not admin_news_tier_broadcast_enabled(league_slug):
        return []
    return [
        (ADMIN_NEWS_SELECT_TIER_UPPER, _TIER_DISPLAY_LABELS["upper"]),
        (ADMIN_NEWS_SELECT_TIER_LOWER, _TIER_DISPLAY_LABELS["lower"]),
    ]


def normalize_broadcast_scope(raw: str | None) -> NewsBroadcastScope | None:
    s = (raw or "").strip().lower()
    if s in ("league", "upper", "lower"):
        return s  # type: ignore[return-value]
    return None


def article_broadcast_scope(article: NewsArticle | object) -> NewsBroadcastScope | None:
    if getattr(article, "team_id", None) is not None:
        return None
    return normalize_broadcast_scope(getattr(article, "broadcast_scope", None))


def tier_broadcast_display_label(
    league_slug: str,
    scope: NewsBroadcastScope,
    league_session: Session | None = None,
) -> str:
    if scope in _TIER_DISPLAY_LABELS:
        return _TIER_DISPLAY_LABELS[scope]
    if league_session is not None and scope in ("upper", "lower"):
        from app.services.relegation import scope_heading

        cfg = get_tier_config(league_session)
        return scope_heading(scope, cfg)  # type: ignore[arg-type]
    return "League"


def news_franchise_tag_label(
    article: NewsArticle | object,
    *,
    league_slug: str,
    league_session: Session | None = None,
    teams_by_id: dict[int, Team] | None = None,
) -> str | None:
    """Byline franchise tag: team name, tier league, or full league."""
    tid = getattr(article, "team_id", None)
    if tid is not None:
        if teams_by_id is not None:
            tm = teams_by_id.get(int(tid))
            if tm is not None:
                return tm.full_display_name()
        if league_session is not None:
            tm = league_session.get(Team, int(tid))
            if tm is not None:
                return tm.full_display_name()
        return None
    scope = article_broadcast_scope(article)
    if scope in ("upper", "lower"):
        return tier_broadcast_display_label(league_slug, scope, league_session)
    return "League"


def parse_admin_news_team_selection(
    raw_tid: str,
    *,
    league_slug: str,
) -> tuple[int | None, NewsBroadcastScope | None, str | None]:
    """
    Parse admin compose ``team_id`` select value.

    Returns (team_id, broadcast_scope, error_message).
    """
    raw = (raw_tid or "").strip()
    low = raw.lower()
    if low == ADMIN_NEWS_SELECT_LEAGUE:
        return None, "league", None
    if low == ADMIN_NEWS_SELECT_TIER_UPPER:
        if not admin_news_tier_broadcast_enabled(league_slug):
            return None, None, "BLUP League broadcasts are not available for this site."
        return None, "upper", None
    if low == ADMIN_NEWS_SELECT_TIER_LOWER:
        if not admin_news_tier_broadcast_enabled(league_slug):
            return None, None, "BLOW League broadcasts are not available for this site."
        return None, "lower", None
    if not raw.isdigit():
        return None, None, "Select a team this article is about, or League."
    return int(raw), None, None


def team_ids_for_broadcast_scope(
    league_session: Session,
    league_slug: str,
    scope: NewsBroadcastScope,
) -> frozenset[int]:
    if scope not in ("upper", "lower"):
        return frozenset()
    if not admin_news_tier_broadcast_enabled(league_slug):
        return frozenset()
    cfg = get_tier_config(league_session)
    teams = list(league_session.scalars(select(Team)).all())
    tier: RelegationTier = scope  # type: ignore[assignment]
    scoped = filter_teams_by_scope(teams, tier, cfg)
    return frozenset(int(t.id) for t in scoped)


def gm_discord_mentions_for_team_ids(
    session: Session,
    league_slug: str,
    team_ids: frozenset[int],
) -> str:
    parts: list[str] = []
    seen_users: set[int] = set()
    for tid in sorted(team_ids):
        for uid in gm_user_ids_for_team(session, league_slug, int(tid)):
            if uid in seen_users:
                continue
            seen_users.add(int(uid))
            user = session.get(User, int(uid))
            if user is None:
                continue
            did = str(getattr(user, "discord_user_id", "") or "").strip()
            if did:
                parts.append(f"<@{did}>")
            else:
                label = gm_discord_name(user)
                if label:
                    parts.append(label)
    return " ".join(parts) if parts else ""


def discord_extra_fields_for_admin_broadcast(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    broadcast_scope: NewsBroadcastScope | None,
) -> dict[str, object]:
    """Discord payload fragments for league-wide or tier-wide admin news."""
    from flask import current_app

    scope = broadcast_scope or "league"
    if scope in ("upper", "lower"):
        team_ids = team_ids_for_broadcast_scope(league_session, league_slug, scope)
        mention = gm_discord_mentions_for_team_ids(site_session, league_slug, team_ids)
        fields: dict[str, object] = {
            "league_wide": True,
            "team_name": tier_broadcast_display_label(league_slug, scope, league_session),
            "news_broadcast_scope": scope,
        }
        if mention:
            fields["team_gm_mention"] = mention
        return fields

    display = str(current_app.config.get("LEAGUE_DISPLAY_NAME") or "League")
    fields = {
        "league_wide": True,
        "team_name": display,
        "news_broadcast_scope": "league",
    }
    from app.services.discord_events import gm_role_mention_for_league

    role_mention = gm_role_mention_for_league(site_session, league_slug)
    if role_mention.startswith("<@"):
        fields["team_gm_mention"] = role_mention
    return fields


def notification_office_label(article: NewsArticle, league_slug: str) -> str:
    scope = article_broadcast_scope(article)
    if scope in ("upper", "lower"):
        return tier_broadcast_display_label(league_slug, scope)
    return "League office"
