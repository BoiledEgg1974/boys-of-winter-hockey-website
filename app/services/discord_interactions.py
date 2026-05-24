"""Discord slash-command interaction helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy import select

from app.league_db import db
from app.models import Game, Team, TeamStanding
from app.services.gm_messaging import unread_count_for_user
from app.services.gm_notifications import unread_notifications_count
from app.services.seasons import get_current_season, season_with_imported_data_fallback
from app.site_models import User

COMMAND_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "inbox",
        "description": "Check whether you have unread BOWL GM Messages.",
    },
    {
        "name": "standings",
        "description": "Show a quick top-five standings snapshot for this league.",
    },
    {
        "name": "nextgame",
        "description": "Show the next scheduled game in this league.",
    },
    {
        "name": "team",
        "description": "Show a quick team lookup.",
        "options": [
            {
                "name": "query",
                "description": "Team name or abbreviation",
                "type": 3,
                "required": True,
            }
        ],
    },
]


def verify_interaction_signature(*, body: bytes, timestamp: str, signature: str, public_key: str) -> bool:
    if not public_key:
        return False
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
    except Exception:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _ephemeral(content: str) -> dict[str, Any]:
    return {"type": 4, "data": {"content": content[:1900], "flags": 64}}


def _discord_user_id(payload: dict[str, Any]) -> str:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    return str(user.get("id") or "").strip()


def _command_option(payload: dict[str, Any], name: str) -> str:
    data = payload.get("data") or {}
    for opt in data.get("options") or []:
        if str(opt.get("name") or "") == name:
            return str(opt.get("value") or "").strip()
    return ""


def _site_user_for_discord(payload: dict[str, Any]) -> User | None:
    discord_id = _discord_user_id(payload)
    if not discord_id:
        return None
    return db.session.scalar(select(User).where(User.discord_user_id == discord_id).limit(1))


def _current_dashboard_season():
    canonical = get_current_season()
    return season_with_imported_data_fallback(db.session, canonical) if canonical else None


def handle_slash_interaction(
    payload: dict[str, Any],
    *,
    league_slug: str | None = None,
) -> dict[str, Any]:
    if int(payload.get("type") or 0) == 1:
        return {"type": 1}
    if int(payload.get("type") or 0) != 2:
        return _ephemeral("Unsupported interaction.")

    data = payload.get("data") or {}
    command = str(data.get("name") or "").strip().lower()
    slug = str(league_slug or current_app.config.get("LEAGUE_SLUG") or "").strip()
    if command == "inbox":
        user = _site_user_for_discord(payload)
        if user is None:
            return _ephemeral("I could not match your Discord account to a BOWL user yet. Ask an admin to add your Discord User ID.")
        unread = unread_count_for_user(slug, int(user.id)) + unread_notifications_count(slug, int(user.id))
        if unread:
            return _ephemeral(f"You have {unread} unread GM Messages / site notification(s): /{slug}/gm/messages")
        return _ephemeral("You are all caught up. No unread GM Messages right now.")

    season = _current_dashboard_season()
    if season is None:
        return _ephemeral("No imported season data is available yet.")

    if command == "standings":
        rows = db.session.execute(
            select(TeamStanding, Team)
            .join(Team, TeamStanding.team_id == Team.id)
            .where(TeamStanding.season_id == season.id)
            .order_by(TeamStanding.points.desc(), TeamStanding.wins.desc(), Team.abbreviation.asc())
            .limit(5)
        ).all()
        if not rows:
            return _ephemeral("No standings data is available yet.")
        lines = ["Top standings:"]
        for i, (st, tm) in enumerate(rows, start=1):
            lines.append(f"{i}. {tm.abbreviation or tm.name}: {st.points} pts ({st.wins}-{st.losses}-{st.ot_losses})")
        return _ephemeral("\n".join(lines))

    if command == "nextgame":
        game = db.session.scalar(
            select(Game)
            .where(Game.season_id == season.id, Game.status != "final")
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            .limit(1)
        )
        if game is None:
            return _ephemeral("No upcoming game is currently scheduled.")
        home = db.session.get(Team, game.home_team_id)
        away = db.session.get(Team, game.away_team_id)
        date_s = game.game_date.isoformat() if game.game_date else "TBD"
        return _ephemeral(f"Next game: {away.abbreviation if away else 'Away'} at {home.abbreviation if home else 'Home'} on {date_s}.")

    if command == "team":
        query = _command_option(payload, "query").casefold()
        if not query:
            return _ephemeral("Tell me which team to look up.")
        team = db.session.scalar(
            select(Team)
            .where((Team.abbreviation.ilike(f"%{query}%")) | (Team.name.ilike(f"%{query}%")))
            .limit(1)
        )
        if team is None:
            return _ephemeral("I could not find that team.")
        st = db.session.scalar(
            select(TeamStanding).where(TeamStanding.season_id == season.id, TeamStanding.team_id == team.id).limit(1)
        )
        if st is None:
            return _ephemeral(f"{team.name} ({team.abbreviation}) has no standings row yet.")
        return _ephemeral(f"{team.name} ({team.abbreviation}): {st.points} pts, {st.wins}-{st.losses}-{st.ot_losses}.")

    return _ephemeral("Unknown BOWL command.")
