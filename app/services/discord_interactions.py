"""Discord slash-command interaction helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy import select

from app.league_db import db
from app.models import Game, Player, Team, TeamStanding
from app.services.gm_messaging import unread_count_for_user
from app.services.gm_notifications import unread_notifications_count
from app.services.player_ratings_csv import player_positions_display_label
from app.services.seasons import get_current_season, season_with_imported_data_fallback
from app.site_models import DiscordChannelRoute, User

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
    {
        "name": "draftstatus",
        "description": "Show the live Draft Hub clock, on-deck team, and recent picks.",
    },
    {
        "name": "draft",
        "description": "Make your Draft Hub pick when your team is on the clock.",
        "options": [
            {
                "name": "player",
                "description": "Player name or site player id",
                "type": 3,
                "required": True,
            }
        ],
    },
    {
        "name": "list",
        "description": "Show the top 20 remaining Draft Hub eligible players.",
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


def _command_channel_id(payload: dict[str, Any]) -> str:
    return str(payload.get("channel_id") or "").strip()


def _channel_check(league_slug: str, event_key: str, payload: dict[str, Any]) -> str | None:
    """Return an error when a slash command is outside its configured channel."""
    row = db.session.scalar(
        select(DiscordChannelRoute)
        .where(
            DiscordChannelRoute.league_slug == league_slug,
            DiscordChannelRoute.event_key == event_key,
        )
        .limit(1)
    )
    if row is None:
        return (
            f"An admin needs to add the Discord route `{event_key}` "
            "on Admin -> Discord Integration before this command can be used."
        )
    if not bool(row.is_enabled):
        return f"The `{event_key}` Discord route is disabled for this league."
    configured = str(row.discord_channel_id or "").strip()
    if not configured:
        return (
            f"An admin needs to set the channel ID for `{event_key}` "
            "on Admin -> Discord Integration."
        )
    actual = _command_channel_id(payload)
    if actual != configured:
        label = str(row.channel_key or row.label or "configured draft channel").strip()
        return f"Use this command in the configured `{label}` channel."
    return None


def _current_dashboard_season():
    canonical = get_current_season()
    return season_with_imported_data_fallback(db.session, canonical) if canonical else None


def _fmt_rating(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def _eligible_remaining_players(league_slug: str) -> tuple[Any | None, list[Player]]:
    from app.services.draft_hub_eligibility_cache import eligible_players_for_board
    from app.services.draft_hub_state import draft_eligibility_params, featured_draft, picked_player_ids

    draft = featured_draft(db.session, league_slug)
    if draft is None or draft.status != "live":
        return draft, []
    picked = picked_player_ids(db.session, int(draft.id))
    params = draft_eligibility_params(draft)
    return draft, eligible_players_for_board(db.session, league_slug, params, picked)


def _player_search_text(player: Player) -> str:
    parts = [
        str(player.id or ""),
        str(player.fhm_player_id or ""),
        str(player.full_name or ""),
    ]
    return " ".join(p for p in parts if p).casefold()


def _resolve_draft_player(query: str, players: list[Player]) -> tuple[Player | None, str | None]:
    q = str(query or "").strip()
    if not q:
        return None, "Tell me which player to draft."
    q_cf = q.casefold()
    if q.isdigit():
        for p in players:
            if str(p.id) == q or str(p.fhm_player_id or "") == q:
                return p, None
    exact = [p for p in players if str(p.full_name or "").casefold() == q_cf]
    if len(exact) == 1:
        return exact[0], None
    matches = [p for p in players if q_cf in _player_search_text(p)]
    if not matches:
        return None, f"No remaining draft-eligible player matched `{q}`."
    if len(matches) == 1:
        return matches[0], None
    lines = [f"Multiple players matched `{q}`. Try the full name or player id:"]
    for p in matches[:8]:
        pos = player_positions_display_label(p) or "-"
        lines.append(f"- {p.full_name} ({pos}) · id `{p.id}` · POT {_fmt_rating(p.overall_potential)}")
    if len(matches) > 8:
        lines.append(f"...and {len(matches) - 8} more.")
    return None, "\n".join(lines)


def _handle_draft_list_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    err = _channel_check(league_slug, "draft_hub_command_list", payload)
    if err:
        return _ephemeral(err)
    draft, players = _eligible_remaining_players(league_slug)
    if draft is None or getattr(draft, "status", None) != "live":
        return _ephemeral("No live Draft Hub draft for this league right now.")
    if not players:
        return _ephemeral("No remaining eligible players found for the live draft.")
    lines = [f"**{draft.name or 'Draft Hub'}** — top 20 remaining eligible players:"]
    for idx, p in enumerate(players[:20], start=1):
        pos = player_positions_display_label(p) or "-"
        lines.append(
            f"{idx}. {p.full_name} ({pos}) · POT {_fmt_rating(p.overall_potential)} "
            f"· ABI {_fmt_rating(p.overall_ability)} · id `{p.id}`"
        )
    return _ephemeral("\n".join(lines))


def _handle_draft_pick_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    err = _channel_check(league_slug, "draft_hub_command_pick", payload)
    if err:
        return _ephemeral(err)
    user = _site_user_for_discord(payload)
    if user is None:
        return _ephemeral(
            "I could not match your Discord account to a BOWL user yet. "
            "Ask an admin to add your Discord User ID."
        )
    draft, players = _eligible_remaining_players(league_slug)
    if draft is None or getattr(draft, "status", None) != "live":
        return _ephemeral("No live Draft Hub draft for this league right now.")
    player, resolve_err = _resolve_draft_player(_command_option(payload, "player"), players)
    if resolve_err:
        return _ephemeral(resolve_err)
    assert player is not None
    from app.services.draft_hub_state import record_pick

    pick_err = record_pick(db.session, draft, int(player.id), int(user.id), "gm")
    if pick_err:
        db.session.rollback()
        return _ephemeral(pick_err)
    db.session.commit()
    pos = player_positions_display_label(player) or "-"
    return _ephemeral(
        f"Draft pick recorded: **{player.full_name}** ({pos}) for {draft.name or 'Draft Hub'}. "
        "The Draft Hub page and Discord pick post will update."
    )


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
    if command == "draftstatus":
        from app.services.draft_hub_discord import build_draft_status_message

        return _ephemeral(build_draft_status_message(db.session, slug))
    if command == "draft":
        return _handle_draft_pick_command(payload, slug)
    if command == "list":
        return _handle_draft_list_command(payload, slug)
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
