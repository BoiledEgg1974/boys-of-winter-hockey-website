"""Discord slash-command interaction helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import current_app
from sqlalchemy import func, or_, select

from app.league_db import db
from app.sqlite_retry import commit_with_sqlite_retry
from app.models import (
    Game,
    HallOfFameMember,
    HistoryAward,
    HistoryChampion,
    Player,
    PlayerGoalieStat,
    PlayerSkaterStat,
    Season,
    Team,
    TeamSeasonRecord,
    TeamStanding,
)
from app.services.ap_service import team_ap_balance
from app.services.draft_pick_ownership import describe_draft_pick_row, owned_draft_picks_for_team
from app.services.gm_messaging import unread_count_for_user
from app.services.gm_notifications import unread_notifications_count
from app.services.player_ratings_csv import player_positions_display_label
from app.services.seasons import get_current_season, season_with_imported_data_fallback
from app.site_models import (
    ApLedgerEntry,
    ApRedemptionRequest,
    BoostLotteryTeamResult,
    DiscordChannelRoute,
    GmLeagueMembership,
    NewsArticle,
    RfaOfferRequest,
    StaffChangeRequest,
    TeamStaffBudget,
    TeamStaffRosterEntry,
    TradeMarketBuyingNeed,
    TradeMarketListing,
    User,
)

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
    {
        "name": "expansionstatus",
        "description": "Show the live Expansion Draft clock and recent picks.",
    },
    {
        "name": "expansionpick",
        "description": "Record an Expansion Draft pick (commissioner only).",
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
        "name": "expansionlist",
        "description": "Show the top 20 remaining Expansion Draft eligible players.",
    },
    {
        "name": "player",
        "description": "Show a quick player lookup.",
        "options": [
            {
                "name": "query",
                "description": "Player name or site/FHM player id",
                "type": 3,
                "required": True,
            }
        ],
    },
    {
        "name": "schedule",
        "description": "Show upcoming games for the league or a team.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
        ],
    },
    {
        "name": "results",
        "description": "Show recent final scores for the league or a team.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
        ],
    },
    {
        "name": "leaders",
        "description": "Show top statistical leaders.",
        "options": [
            {
                "name": "category",
                "description": "Leader category",
                "type": 3,
                "required": True,
                "choices": [
                    {"name": "Points", "value": "points"},
                    {"name": "Goals", "value": "goals"},
                    {"name": "Assists", "value": "assists"},
                    {"name": "Goalies", "value": "goalies"},
                    {"name": "Rookies", "value": "rookies"},
                ],
            }
        ],
    },
    {
        "name": "drafteligible",
        "description": "Show top public Draft Eligible page players.",
        "options": [
            {"name": "position", "description": "Optional position filter (C, LW, RW, D, G)", "type": 3, "required": False},
            {"name": "query", "description": "Optional player name filter", "type": 3, "required": False},
        ],
    },
    {
        "name": "picks",
        "description": "Show draft picks owned by a team.",
        "options": [
            {"name": "team", "description": "Team name or abbreviation", "type": 3, "required": True},
            {"name": "year", "description": "Optional draft year", "type": 4, "required": False},
        ],
    },
    {"name": "ap", "description": "Show your team's AP balance and recent ledger items."},
    {
        "name": "boosts",
        "description": "Show Boost Lottery tracker totals.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
        ],
    },
    {
        "name": "tradeblock",
        "description": "Show Trade Market selling/buying posts.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
        ],
    },
    {
        "name": "rfa",
        "description": "Show active RFA offer requests.",
        "options": [
            {"name": "team", "description": "Optional offering/rights team", "type": 3, "required": False}
        ],
    },
    {
        "name": "staff",
        "description": "Show staff roster and pending requests for a team.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
        ],
    },
    {
        "name": "news",
        "description": "Show recent league news headlines.",
        "options": [
            {"name": "count", "description": "Number of headlines (1-5)", "type": 4, "required": False}
        ],
    },
    {
        "name": "history",
        "description": "Show a player's awards/Hall of Fame history.",
        "options": [
            {"name": "player", "description": "Player name or id", "type": 3, "required": True}
        ],
    },
    {"name": "champions", "description": "Show recent league champions."},
    {
        "name": "predict",
        "description": "Post playoff series predictions for a round (admin).",
        "default_member_permissions": "8",
        "options": [
            {
                "name": "round",
                "description": "Round to post (omit when only one round is open)",
                "type": 3,
                "required": False,
                "choices": [
                    {"name": "First round", "value": "first"},
                    {"name": "Second round", "value": "second"},
                    {"name": "Conference finals", "value": "conference"},
                    {"name": "Championship", "value": "championship"},
                    {"name": "All open rounds", "value": "all"},
                ],
            }
        ],
    },
    {
        "name": "records",
        "description": "Show team record info.",
        "options": [
            {"name": "team", "description": "Optional team name or abbreviation", "type": 3, "required": False}
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


def _subcommand_name(payload: dict[str, Any]) -> str:
    data = payload.get("data") or {}
    for opt in data.get("options") or []:
        if int(opt.get("type") or 0) == 1:
            return str(opt.get("name") or "").strip().lower()
    return ""


def _subcommand_option(payload: dict[str, Any], name: str) -> str:
    data = payload.get("data") or {}
    for opt in data.get("options") or []:
        if int(opt.get("type") or 0) == 1:
            for nested in opt.get("options") or []:
                if str(nested.get("name") or "") == name:
                    return str(nested.get("value") or "").strip()
    return ""


def _command_option_int(payload: dict[str, Any], name: str, default: int | None = None) -> int | None:
    raw = _command_option(payload, name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _site_user_for_discord(payload: dict[str, Any]) -> User | None:
    discord_id = _discord_user_id(payload)
    if not discord_id:
        return None
    return db.session.scalar(select(User).where(User.discord_user_id == discord_id).limit(1))


def _command_channel_id(payload: dict[str, Any]) -> str:
    return str(payload.get("channel_id") or "").strip()


def _is_discord_admin(payload: dict[str, Any]) -> bool:
    member = payload.get("member") or {}
    raw = member.get("permissions")
    if raw is None:
        return False
    try:
        perms = int(raw)
    except (TypeError, ValueError):
        return False
    return bool(perms & 0x8)


def _playoff_predictions_route_ready(league_slug: str) -> str | None:
    row = db.session.scalar(
        select(DiscordChannelRoute)
        .where(
            DiscordChannelRoute.league_slug == league_slug,
            DiscordChannelRoute.event_key == "playoff_predictions",
        )
        .limit(1)
    )
    if row is None:
        return (
            "An admin needs to add the Discord route `playoff_predictions` "
            "on Admin -> Discord Integration."
        )
    if not bool(row.is_enabled):
        return "The `playoff_predictions` Discord route is disabled for this league."
    configured = str(row.discord_channel_id or "").strip()
    if not configured:
        return (
            "Set the channel ID for `playoff_predictions` "
            "on Admin -> Discord Integration."
        )
    return None


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


def _site_path(league_slug: str, path: str) -> str:
    return f"/{league_slug}/{str(path or '').lstrip('/')}"


def _fmt_date(value: object) -> str:
    if value is None:
        return "TBD"
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _team_label(team: Team | None) -> str:
    if team is None:
        return "Team"
    return str(team.abbreviation or team.full_display_name() or f"Team {team.id}")


def _resolve_team(query: str) -> Team | None:
    q = str(query or "").strip()
    if not q:
        return None
    if q.isdigit():
        team = db.session.get(Team, int(q))
        if team is not None:
            return team
    exact = db.session.scalar(select(Team).where(func.lower(Team.abbreviation) == q.lower()).limit(1))
    if exact is not None:
        return exact
    return db.session.scalar(
        select(Team)
        .where(
            or_(
                Team.abbreviation.ilike(f"%{q}%"),
                Team.name.ilike(f"%{q}%"),
                Team.nickname.ilike(f"%{q}%"),
            )
        )
        .order_by(Team.abbreviation.asc())
        .limit(1)
    )


def _active_membership_for_payload(payload: dict[str, Any], league_slug: str) -> GmLeagueMembership | None:
    user = _site_user_for_discord(payload)
    if user is None:
        return None
    return db.session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.user_id == int(user.id),
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "approved",
        )
        .limit(1)
    )


def _team_from_option_or_membership(
    payload: dict[str, Any],
    league_slug: str,
    *,
    option_name: str = "team",
    require: bool = False,
) -> tuple[Team | None, str | None]:
    query = _command_option(payload, option_name)
    if query:
        team = _resolve_team(query)
        if team is None:
            return None, f"I could not find a team matching `{query}`."
        return team, None
    mem = _active_membership_for_payload(payload, league_slug)
    if mem is not None:
        team = db.session.get(Team, int(mem.team_id))
        if team is not None:
            return team, None
    if require:
        return None, "Tell me which team to use."
    return None, None


def _resolve_player_any(query: str) -> tuple[Player | None, str | None]:
    q = str(query or "").strip()
    if not q:
        return None, "Tell me which player to look up."
    if q.isdigit():
        player = db.session.get(Player, int(q))
        if player is not None:
            return player, None
        player = db.session.scalar(select(Player).where(Player.fhm_player_id == q).limit(1))
        if player is not None:
            return player, None
    exact = list(db.session.scalars(select(Player).where(func.lower(Player.full_name) == q.lower()).limit(3)).all())
    if len(exact) == 1:
        return exact[0], None
    matches = list(
        db.session.scalars(
            select(Player)
            .where(Player.full_name.ilike(f"%{q}%"))
            .order_by(Player.full_name.asc())
            .limit(9)
        ).all()
    )
    if not matches:
        return None, f"No player matched `{q}`."
    if len(matches) == 1:
        return matches[0], None
    lines = [f"Multiple players matched `{q}`. Try the full name or id:"]
    for p in matches[:8]:
        lines.append(f"- {p.full_name} ({player_positions_display_label(p) or '-'}) · id `{p.id}`")
    return None, "\n".join(lines)


def _game_line(game: Game) -> str:
    home = db.session.get(Team, int(game.home_team_id))
    away = db.session.get(Team, int(game.away_team_id))
    prefix = _fmt_date(game.game_date)
    if str(game.status or "").lower() == "final":
        return (
            f"{prefix}: {_team_label(away)} {game.away_score if game.away_score is not None else '-'} "
            f"at {_team_label(home)} {game.home_score if game.home_score is not None else '-'}"
        )
    return f"{prefix}: {_team_label(away)} at {_team_label(home)}"


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
    commit_with_sqlite_retry(db.session)
    pos = player_positions_display_label(player) or "-"
    return _ephemeral(
        f"Draft pick recorded: **{player.full_name}** ({pos}) for {draft.name or 'Draft Hub'}. "
        "The Draft Hub page and Discord pick post will update."
    )


def _expansion_eligible_remaining_players(league_slug: str) -> tuple[Any | None, list[Player], str | None]:
    from app.services.expansion_draft_state import (
        eligible_players_for_board,
        featured_expansion_draft,
        slots_ordered,
    )

    draft = featured_expansion_draft(db.session, league_slug)
    if draft is None or draft.status != "live":
        return draft, [], None
    phase_filter = None
    exp_team_id = None
    phase_label = None
    slots = slots_ordered(db.session, draft.id)
    if draft.current_slot_index < len(slots):
        cs = slots[draft.current_slot_index]
        if not cs.forfeited:
            phase_filter = str(cs.phase or "")
            exp_team_id = int(cs.team_id)
            ph = str(cs.phase or "").strip()
            if ph:
                phase_label = ph[0].upper() + ph[1:] if len(ph) > 1 else ph.upper()
    players = eligible_players_for_board(
        db.session,
        draft,
        phase=phase_filter,
        expansion_team_id=exp_team_id,
    )
    return draft, players, phase_label


def _handle_expansion_list_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    err = _channel_check(league_slug, "expansion_draft_command_list", payload)
    if err:
        return _ephemeral(err)
    draft, players, phase_label = _expansion_eligible_remaining_players(league_slug)
    if draft is None or getattr(draft, "status", None) != "live":
        return _ephemeral("No live Expansion Draft for this league right now.")
    if not players:
        return _ephemeral("No remaining eligible players found for the live expansion draft.")
    phase_bit = f" ({phase_label} phase)" if phase_label else ""
    lines = [f"**{draft.name or 'Expansion draft'}**{phase_bit} — top 20 remaining eligible players:"]
    for idx, p in enumerate(players[:20], start=1):
        pos = player_positions_display_label(p) or "-"
        lines.append(
            f"{idx}. {p.full_name} ({pos}) · POT {_fmt_rating(p.overall_potential)} "
            f"· ABI {_fmt_rating(p.overall_ability)} · id `{p.id}`"
        )
    return _ephemeral("\n".join(lines))


def _handle_expansion_pick_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    err = _channel_check(league_slug, "expansion_draft_command_pick", payload)
    if err:
        return _ephemeral(err)
    user = _site_user_for_discord(payload)
    if user is None:
        return _ephemeral(
            "I could not match your Discord account to a BOWL user yet. "
            "Ask an admin to add your Discord User ID."
        )
    from app.auth_login import league_hub_staff

    if not league_hub_staff(user):
        return _ephemeral("Only league staff can record expansion draft picks.")
    draft, players, _phase_label = _expansion_eligible_remaining_players(league_slug)
    if draft is None or getattr(draft, "status", None) != "live":
        return _ephemeral("No live Expansion Draft for this league right now.")
    player, resolve_err = _resolve_draft_player(_command_option(payload, "player"), players)
    if resolve_err:
        return _ephemeral(resolve_err)
    assert player is not None
    from app.services.expansion_draft_state import resolve_admin_pick

    pick_err = resolve_admin_pick(db.session, draft, int(player.id), int(user.id))
    if pick_err:
        db.session.rollback()
        return _ephemeral(pick_err)
    commit_with_sqlite_retry(db.session)
    pos = player_positions_display_label(player) or "-"
    return _ephemeral(
        f"Expansion pick recorded: **{player.full_name}** ({pos}) for {draft.name or 'Expansion draft'}. "
        "The Expansion Draft Hub page and Discord pick post will update."
    )


def _handle_player_command(payload: dict[str, Any], league_slug: str, season: Season) -> dict[str, Any]:
    player, err = _resolve_player_any(_command_option(payload, "query"))
    if err:
        return _ephemeral(err)
    assert player is not None
    team = db.session.get(Team, int(player.current_team_id)) if player.current_team_id else None
    pos = player_positions_display_label(player) or str(player.position or "-")
    lines = [
        f"**{player.full_name}** ({pos})",
        f"Team: {_team_label(team)} · ABI {_fmt_rating(player.overall_ability)} · POT {_fmt_rating(player.overall_potential)}",
    ]
    sk = db.session.scalar(
        select(PlayerSkaterStat)
        .where(PlayerSkaterStat.season_id == season.id, PlayerSkaterStat.player_id == player.id, PlayerSkaterStat.stat_segment == "rs")
        .limit(1)
    )
    gk = db.session.scalar(
        select(PlayerGoalieStat)
        .where(PlayerGoalieStat.season_id == season.id, PlayerGoalieStat.player_id == player.id, PlayerGoalieStat.stat_segment == "rs")
        .limit(1)
    )
    if sk:
        lines.append(f"Stats: {sk.gp} GP, {sk.goals}-{sk.assists}-{sk.points}, {sk.pim} PIM")
    elif gk:
        sv = f"{float(gk.sv_pct):.3f}" if gk.sv_pct is not None else "-"
        gaa = f"{float(gk.gaa):.2f}" if gk.gaa is not None else "-"
        lines.append(f"Stats: {gk.gp} GP, {gk.wins}-{gk.losses}-{gk.otl}, {sv} SV%, {gaa} GAA")
    lines.append(f"Link: {_site_path(league_slug, f'player/{player.id}')}")
    return _ephemeral("\n".join(lines))


def _handle_schedule_command(payload: dict[str, Any], season: Season) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, str(current_app.config.get("LEAGUE_SLUG") or ""))
    if err:
        return _ephemeral(err)
    conditions = [Game.season_id == season.id, Game.status != "final"]
    if team is not None:
        conditions.append(or_(Game.home_team_id == team.id, Game.away_team_id == team.id))
    games = list(
        db.session.scalars(select(Game).where(*conditions).order_by(Game.game_date.asc().nulls_last(), Game.id.asc()).limit(5)).all()
    )
    if not games:
        return _ephemeral("No upcoming games found.")
    title = f"Next games for {_team_label(team)}:" if team else "Next league games:"
    return _ephemeral("\n".join([title, *(_game_line(g) for g in games)]))


def _handle_results_command(payload: dict[str, Any], season: Season) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, str(current_app.config.get("LEAGUE_SLUG") or ""))
    if err:
        return _ephemeral(err)
    conditions = [Game.season_id == season.id, Game.status == "final"]
    if team is not None:
        conditions.append(or_(Game.home_team_id == team.id, Game.away_team_id == team.id))
    games = list(
        db.session.scalars(select(Game).where(*conditions).order_by(Game.game_date.desc().nulls_last(), Game.id.desc()).limit(5)).all()
    )
    if not games:
        return _ephemeral("No final scores found.")
    title = f"Recent results for {_team_label(team)}:" if team else "Recent league results:"
    return _ephemeral("\n".join([title, *(_game_line(g) for g in games)]))


def _handle_leaders_command(payload: dict[str, Any], season: Season) -> dict[str, Any]:
    cat = (_command_option(payload, "category") or "points").lower()
    if cat == "goalies":
        rows = db.session.execute(
            select(PlayerGoalieStat, Player)
            .join(Player, PlayerGoalieStat.player_id == Player.id)
            .where(PlayerGoalieStat.season_id == season.id, PlayerGoalieStat.stat_segment == "rs")
            .order_by(PlayerGoalieStat.wins.desc(), PlayerGoalieStat.sv_pct.desc().nulls_last())
            .limit(10)
        ).all()
        if not rows:
            return _ephemeral("No goalie leaders found.")
        lines = ["Goalie leaders:"]
        for i, (st, p) in enumerate(rows, start=1):
            sv = f"{float(st.sv_pct):.3f}" if st.sv_pct is not None else "-"
            lines.append(f"{i}. {p.full_name}: {st.wins} W, {sv} SV%, {_fmt_rating(st.gaa)} GAA")
        return _ephemeral("\n".join(lines))
    attr = {"goals": PlayerSkaterStat.goals, "assists": PlayerSkaterStat.assists}.get(cat, PlayerSkaterStat.points)
    rows = db.session.execute(
        select(PlayerSkaterStat, Player)
        .join(Player, PlayerSkaterStat.player_id == Player.id)
        .where(PlayerSkaterStat.season_id == season.id, PlayerSkaterStat.stat_segment == "rs")
        .order_by(attr.desc(), PlayerSkaterStat.points.desc())
        .limit(10)
    ).all()
    if cat == "rookies":
        rows = rows[:10]
    if not rows:
        return _ephemeral("No skater leaders found.")
    label = {"goals": "Goal", "assists": "Assist", "rookies": "Rookie point"}.get(cat, "Point")
    lines = [f"{label} leaders:"]
    for i, (st, p) in enumerate(rows, start=1):
        value = getattr(st, "goals" if cat == "goals" else "assists" if cat == "assists" else "points")
        lines.append(f"{i}. {p.full_name}: {value} ({st.goals}-{st.assists}-{st.points})")
    return _ephemeral("\n".join(lines))


def _handle_drafteligible_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    from app.routes.main import _prospect_pos_matches
    from app.services.draft_hub_eligibility import (
        draft_eligible_page_params_for_league,
        draft_eligible_timeline_year_for_league,
        eligible_players_ordered,
    )

    season = _current_dashboard_season()
    timeline = draft_eligible_timeline_year_for_league(
        league_slug,
        int(season.start_year) if season and season.start_year else None,
        int(season.end_year) if season and season.end_year else None,
        datetime.utcnow().year,
    )
    params = draft_eligible_page_params_for_league(league_slug, timeline)
    players = eligible_players_ordered(db.session, league_slug, params)
    pos = _command_option(payload, "position")
    q = _command_option(payload, "query").casefold()
    if pos:
        players = [p for p in players if _prospect_pos_matches(p.position, pos)]
    if q:
        players = [p for p in players if q in str(p.full_name or "").casefold()]
    if not players:
        return _ephemeral("No draft-eligible players matched that filter.")
    lines = ["Top Draft Eligible players:"]
    for i, p in enumerate(players[:15], start=1):
        lines.append(f"{i}. {p.full_name} ({player_positions_display_label(p) or '-'}) · POT {_fmt_rating(p.overall_potential)} · id `{p.id}`")
    return _ephemeral("\n".join(lines))


def _handle_picks_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, league_slug, require=True)
    if err:
        return _ephemeral(err)
    assert team is not None
    year = _command_option_int(payload, "year")
    rows = owned_draft_picks_for_team(db.session, league_slug=league_slug, team_id=int(team.id))
    if year is not None:
        rows = [r for r in rows if int(r.draft_year) == year]
    if not rows:
        return _ephemeral(f"No owned draft picks found for {_team_label(team)}.")
    lines = [f"Draft picks owned by {_team_label(team)}:"]
    for row in rows[:20]:
        orig = db.session.get(Team, int(row.original_team_id)) if row.original_team_id else None
        owner = db.session.get(Team, int(row.owner_team_id)) if row.owner_team_id else None
        lines.append(f"- {describe_draft_pick_row(row, original_team=orig, owner_team=owner)}")
    return _ephemeral("\n".join(lines))


def _handle_ap_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    mem = _active_membership_for_payload(payload, league_slug)
    if mem is None:
        return _ephemeral("I could not find your approved GM membership for this league.")
    team = db.session.get(Team, int(mem.team_id))
    bal = team_ap_balance(league_slug, int(mem.team_id))
    rows = list(
        db.session.scalars(
            select(ApLedgerEntry)
            .where(ApLedgerEntry.league_slug == league_slug, ApLedgerEntry.team_id == int(mem.team_id))
            .order_by(ApLedgerEntry.created_at.desc())
            .limit(5)
        ).all()
    )
    lines = [f"{_team_label(team)} AP balance: **{bal}**"]
    for r in rows:
        sign = "+" if int(r.delta) >= 0 else ""
        lines.append(f"- {sign}{r.delta} · {r.reason_code} · {_fmt_date(r.created_at.date() if r.created_at else None)}")
    pending = db.session.scalar(
        select(func.count()).select_from(ApRedemptionRequest).where(
            ApRedemptionRequest.league_slug == league_slug,
            ApRedemptionRequest.team_id == int(mem.team_id),
            ApRedemptionRequest.status == "pending",
        )
    )
    if pending:
        lines.append(f"Pending redemptions: {int(pending)}")
    return _ephemeral("\n".join(lines))


def _handle_boosts_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, league_slug)
    if err:
        return _ephemeral(err)
    query = select(BoostLotteryTeamResult).where(BoostLotteryTeamResult.league_slug == league_slug)
    if team is not None:
        query = query.where(BoostLotteryTeamResult.team_id == int(team.id))
    rows = list(db.session.scalars(query).all())
    rows.sort(key=lambda r: (-(int(r.gold_count or 0) + int(r.silver_count or 0)), -int(r.gold_count or 0)))
    if not rows:
        return _ephemeral("No Boost Lottery tracker totals found yet.")
    lines = [f"Boost Lottery totals{' for ' + _team_label(team) if team else ''}:"]
    for r in rows[:10]:
        tm = db.session.get(Team, int(r.team_id))
        total = int(r.gold_count or 0) + int(r.silver_count or 0)
        lines.append(f"- {_team_label(tm)}: {r.gold_count} gold, {r.silver_count} silver ({total} total)")
    return _ephemeral("\n".join(lines))


def _handle_tradeblock_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, league_slug)
    if err:
        return _ephemeral(err)
    listing_query = select(TradeMarketListing).where(TradeMarketListing.league_slug == league_slug, TradeMarketListing.status == "active")
    buying_query = select(TradeMarketBuyingNeed).where(TradeMarketBuyingNeed.league_slug == league_slug, TradeMarketBuyingNeed.status == "active")
    if team is not None:
        listing_query = listing_query.where(TradeMarketListing.team_id == int(team.id))
        buying_query = buying_query.where(TradeMarketBuyingNeed.team_id == int(team.id))
    listings = list(db.session.scalars(listing_query.order_by(TradeMarketListing.updated_at.desc()).limit(5)).all())
    needs = list(db.session.scalars(buying_query.order_by(TradeMarketBuyingNeed.updated_at.desc()).limit(5)).all())
    if not listings and not needs:
        return _ephemeral("No active Trade Market posts found.")
    lines = [f"Trade Market{' for ' + _team_label(team) if team else ''}:"]
    for row in listings:
        tm = db.session.get(Team, int(row.team_id))
        lines.append(f"- Selling {_team_label(tm)}: {row.asset_type} {row.asset_ref} · ask: {row.asking_price or '-'}")
    for row in needs:
        tm = db.session.get(Team, int(row.team_id))
        lines.append(f"- Buying {_team_label(tm)}: {row.category} · {row.note or '-'}")
    return _ephemeral("\n".join(lines))


def _handle_rfa_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, league_slug)
    if err:
        return _ephemeral(err)
    query = select(RfaOfferRequest).where(RfaOfferRequest.league_slug == league_slug)
    if team is not None:
        query = query.where(or_(RfaOfferRequest.offering_team_id == int(team.id), RfaOfferRequest.rights_team_id == int(team.id)))
    rows = list(db.session.scalars(query.order_by(RfaOfferRequest.created_at.desc()).limit(10)).all())
    if not rows:
        return _ephemeral("No RFA offers found.")
    lines = [f"RFA offers{' for ' + _team_label(team) if team else ''}:"]
    for r in rows[:8]:
        pl = db.session.get(Player, int(r.player_id))
        off = db.session.get(Team, int(r.offering_team_id))
        rights = db.session.get(Team, int(r.rights_team_id))
        lines.append(f"- {pl.full_name if pl else 'Player'}: {_team_label(off)} offer, rights {_team_label(rights)} · {r.status} · ${int(r.offer_salary):,} x {r.offer_years}")
    return _ephemeral("\n".join(lines))


def _handle_staff_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, league_slug, require=True)
    if err:
        return _ephemeral(err)
    assert team is not None
    season = _current_dashboard_season()
    start_year = int(season.start_year or season.end_year or 0) if season else 0
    roster = list(
        db.session.scalars(
            select(TeamStaffRosterEntry)
            .where(TeamStaffRosterEntry.league_slug == league_slug, TeamStaffRosterEntry.team_id == int(team.id), TeamStaffRosterEntry.fired_at.is_(None))
            .order_by(TeamStaffRosterEntry.role.asc(), TeamStaffRosterEntry.staff_name.asc())
            .limit(8)
        ).all()
    )
    budget = db.session.scalar(
        select(TeamStaffBudget).where(TeamStaffBudget.league_slug == league_slug, TeamStaffBudget.team_id == int(team.id), TeamStaffBudget.season_start_year == start_year).limit(1)
    ) if start_year else None
    pending = list(
        db.session.scalars(
            select(StaffChangeRequest)
            .where(StaffChangeRequest.league_slug == league_slug, StaffChangeRequest.team_id == int(team.id), StaffChangeRequest.status == "pending")
            .order_by(StaffChangeRequest.created_at.desc())
            .limit(5)
        ).all()
    )
    lines = [f"Staff for {_team_label(team)}:"]
    if budget:
        lines.append(f"Budget: ${int(budget.budget_amount):,}")
    lines.extend([f"- {r.role}: {r.staff_name}" for r in roster] or ["- No approved staff roster entries found."])
    if pending:
        lines.append("Pending requests:")
        lines.extend(f"- {r.request_type} {r.staff_name} ({r.role or '-'})" for r in pending)
    return _ephemeral("\n".join(lines))


def _handle_news_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    count = max(1, min(5, _command_option_int(payload, "count", 3) or 3))
    rows = list(
        db.session.scalars(
            select(NewsArticle)
            .where(NewsArticle.league_slug == league_slug, NewsArticle.status == "published")
            .order_by(NewsArticle.published_at.desc(), NewsArticle.created_at.desc())
            .limit(count)
        ).all()
    )
    if not rows:
        return _ephemeral("No published news found.")
    lines = ["Latest news:"]
    for r in rows:
        lines.append(f"- {r.title} · {_fmt_date(r.published_at.date() if r.published_at else r.created_at.date())} · {_site_path(league_slug, 'headlines')}")
    return _ephemeral("\n".join(lines))


def _handle_history_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    player, err = _resolve_player_any(_command_option(payload, "player"))
    if err:
        return _ephemeral(err)
    assert player is not None
    hof = db.session.scalar(select(HallOfFameMember).where(HallOfFameMember.player_id == int(player.id)).limit(1))
    awards = list(
        db.session.execute(
            select(HistoryAward, Season)
            .join(Season, HistoryAward.season_id == Season.id)
            .where(HistoryAward.player_id == int(player.id))
            .order_by(Season.start_year.desc().nulls_last())
            .limit(8)
        ).all()
    )
    lines = [f"History for **{player.full_name}**:"]
    if hof:
        lines.append(f"Hall of Fame: inducted {hof.inducted_year}")
    if awards:
        for award, season in awards:
            lines.append(f"- {season.label}: {award.award_name}")
    if len(lines) == 1:
        lines.append("No awards or Hall of Fame entry found.")
    lines.append(f"Link: {_site_path(league_slug, f'player/{player.id}')}")
    return _ephemeral("\n".join(lines))


def _handle_champions_command() -> dict[str, Any]:
    rows = list(
        db.session.execute(
            select(HistoryChampion, Season, Team)
            .join(Season, HistoryChampion.season_id == Season.id)
            .join(Team, HistoryChampion.team_id == Team.id)
            .order_by(Season.start_year.desc().nulls_last(), HistoryChampion.id.desc())
            .limit(8)
        ).all()
    )
    if not rows:
        return _ephemeral("No champion history found.")
    lines = ["Recent champions:"]
    for champ, season, team in rows:
        trophy = f" ({champ.trophy})" if champ.trophy else ""
        lines.append(f"- {season.label}: {team.full_display_name()}{trophy}")
    return _ephemeral("\n".join(lines))


def _handle_predict_command(payload: dict[str, Any], league_slug: str) -> dict[str, Any]:
    if not _is_discord_admin(payload):
        return _ephemeral("This command is for server administrators only.")
    err = _channel_check(league_slug, "playoff_predictions", payload)
    if err:
        return _ephemeral(err)
    from app.services.discord_events import enqueue_discord_event
    from app.services.playoff_discord_predictions import (
        build_playoff_predictions_discord_payload,
        format_predict_round_help,
        list_prediction_rounds,
        normalize_predict_round_filter,
    )
    from app.services.playoff_bracket import playoff_bracket_payload
    from app.services.seasons import get_current_season, season_with_imported_data_fallback

    season = season_with_imported_data_fallback(db.session, get_current_season())
    if season is None:
        return _ephemeral("No imported season data is available yet.")
    bracket = playoff_bracket_payload(int(season.id), include_team_logos=False)
    if bracket.get("empty"):
        return _ephemeral(str(bracket.get("message") or "No playoff bracket is available yet."))

    round_raw = _command_option(payload, "round")
    round_filter: str | None = "__pending__"
    if not round_raw:
        open_rounds = list_prediction_rounds(bracket)
        if not open_rounds:
            return _ephemeral(
                "No playoff series need predictions right now. "
                "Completed and projected-only matchups are skipped."
            )
        if len(open_rounds) == 1:
            round_filter = str(open_rounds[0].get("label") or "").strip() or None
        else:
            return _ephemeral(format_predict_round_help(open_rounds))

    if round_filter == "__pending__":
        normalized = normalize_predict_round_filter(round_raw)
        if normalized is None:
            return _ephemeral(
                "I could not match that round. Pick **round** from the command menu: "
                "`first`, `second`, `conference`, `championship`, or `all`."
            )
        round_filter = None if normalized == "__all__" else normalized

    result = build_playoff_predictions_discord_payload(
        db.session,
        league_slug=league_slug,
        round_filter=round_filter,
        bracket=bracket,
        season=season,
    )
    if result.get("error"):
        return _ephemeral(str(result["error"]))
    disc_payload = result["payload"]
    user = _site_user_for_discord(payload)
    row = enqueue_discord_event(
        db.session,
        league_slug=league_slug,
        event_key="playoff_predictions",
        payload=disc_payload,
        created_by_user_id=int(user.id) if user else None,
        source_type="playoff_predictions_post",
        source_id=str(disc_payload.get("source_id") or ""),
    )
    if row is None:
        db.session.rollback()
        return _ephemeral(
            "Could not queue the playoff predictions post. Check Discord Integration settings."
        )
    commit_with_sqlite_retry(db.session)
    count = int(disc_payload.get("series_count") or 0)
    round_note = f" for **{round_filter}**" if round_filter else ""
    return _ephemeral(
        f"Queued playoff predictions for {count} series{round_note} to the configured playoff-predictions channel."
    )


def _handle_records_command(payload: dict[str, Any], season: Season) -> dict[str, Any]:
    team, err = _team_from_option_or_membership(payload, str(current_app.config.get("LEAGUE_SLUG") or ""))
    if err:
        return _ephemeral(err)
    if team is not None:
        st = db.session.scalar(select(TeamStanding).where(TeamStanding.season_id == season.id, TeamStanding.team_id == int(team.id)).limit(1))
        if st is None:
            return _ephemeral(f"No current standings record found for {_team_label(team)}.")
        return _ephemeral(
            f"{team.full_display_name()}: {_discord_standings_record(st)}, "
            f"{int(st.pts or 0)} pts, GF/GA {int(st.gf or 0)}/{int(st.ga or 0)}, "
            f"streak {st.streak or '-'}."
        )
    rec = db.session.scalar(select(TeamSeasonRecord).where(TeamSeasonRecord.pts.isnot(None)).order_by(TeamSeasonRecord.pts.desc()).limit(1))
    if rec is None:
        return _ephemeral("No team record data found.")
    return _ephemeral(
        f"Top season record: {rec.team_name_override or 'Team'} {rec.season_year_label}: "
        f"{int(rec.gp or 0)} GP, {int(rec.w or 0)} W, {int(rec.l or 0)} L, "
        f"{int(rec.t_otl or 0)} T, {int(rec.pts or 0)} pts."
    )


def _discord_standings_record(st: TeamStanding) -> str:
    """Discord standings should mirror imported GP/W/L/T columns, not OTL labels."""
    gp = int(st.gp or 0)
    if gp <= 0:
        gp = int(st.standing_gp_display() or 0)
    return (
        f"{gp} GP, {int(st.w or 0)} W, {int(st.l or 0)} L, "
        f"{int(st.ties or 0)} T"
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
    if command == "expansionstatus":
        from app.services.expansion_draft_discord import build_expansion_status_message

        return _ephemeral(build_expansion_status_message(db.session, slug))
    if command == "draft":
        return _handle_draft_pick_command(payload, slug)
    if command == "expansionpick":
        return _handle_expansion_pick_command(payload, slug)
    if command == "list":
        return _handle_draft_list_command(payload, slug)
    if command == "expansionlist":
        return _handle_expansion_list_command(payload, slug)
    if command == "inbox":
        user = _site_user_for_discord(payload)
        if user is None:
            return _ephemeral("I could not match your Discord account to a BOWL user yet. Ask an admin to add your Discord User ID.")
        unread = unread_count_for_user(slug, int(user.id)) + unread_notifications_count(slug, int(user.id))
        if unread:
            return _ephemeral(f"You have {unread} unread GM Messages / site notification(s): /{slug}/gm/messages")
        return _ephemeral("You are all caught up. No unread GM Messages right now.")
    if command == "drafteligible":
        return _handle_drafteligible_command(payload, slug)
    if command == "picks":
        return _handle_picks_command(payload, slug)
    if command == "ap":
        return _handle_ap_command(payload, slug)
    if command == "boosts":
        return _handle_boosts_command(payload, slug)
    if command == "tradeblock":
        return _handle_tradeblock_command(payload, slug)
    if command == "rfa":
        return _handle_rfa_command(payload, slug)
    if command == "staff":
        return _handle_staff_command(payload, slug)
    if command == "news":
        return _handle_news_command(payload, slug)
    if command == "history":
        return _handle_history_command(payload, slug)
    if command == "champions":
        return _handle_champions_command()
    if command == "predict":
        return _handle_predict_command(payload, slug)

    season = _current_dashboard_season()
    if season is None:
        return _ephemeral("No imported season data is available yet.")

    if command == "player":
        return _handle_player_command(payload, slug, season)
    if command == "schedule":
        return _handle_schedule_command(payload, season)
    if command == "results":
        return _handle_results_command(payload, season)
    if command == "leaders":
        return _handle_leaders_command(payload, season)
    if command == "records":
        return _handle_records_command(payload, season)

    if command == "standings":
        rows = db.session.execute(
            select(TeamStanding, Team)
            .join(Team, TeamStanding.team_id == Team.id)
            .where(TeamStanding.season_id == season.id)
            .order_by(TeamStanding.pts.desc(), TeamStanding.w.desc(), Team.abbreviation.asc())
            .limit(5)
        ).all()
        if not rows:
            return _ephemeral("No standings data is available yet.")
        lines = ["Top standings:"]
        for i, (st, tm) in enumerate(rows, start=1):
            lines.append(
                f"{i}. {tm.abbreviation or tm.name}: {int(st.pts or 0)} pts "
                f"({_discord_standings_record(st)})"
            )
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
        return _ephemeral(
            f"{team.name} ({team.abbreviation}): {int(st.pts or 0)} pts, "
            f"{_discord_standings_record(st)}."
        )

    return _ephemeral("Unknown BOWL command.")
