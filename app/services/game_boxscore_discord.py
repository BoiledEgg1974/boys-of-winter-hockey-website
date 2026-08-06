"""Enqueue per-team Discord boxscore posts when games become final on import."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, Team
from app.services.discord_events import (
    GAME_BOXSCORE_EVENT_KEY,
    build_league_public_url,
    ensure_game_boxscore_team_channels,
    enqueue_discord_event,
    is_discord_event_route_active,
    resolve_game_boxscore_team_channel_id,
)

_log = logging.getLogger(__name__)

# Newly final game ids detected during schedule import (boxscores load later).
_stashed_newly_final_game_ids: set[int] = set()


def stash_newly_final_game_ids(game_ids: set[int] | list[int] | None) -> int:
    """Remember games that flipped to final this import for later Discord enqueue."""
    if not game_ids:
        return 0
    before = len(_stashed_newly_final_game_ids)
    for gid in game_ids:
        try:
            _stashed_newly_final_game_ids.add(int(gid))
        except (TypeError, ValueError):
            continue
    return len(_stashed_newly_final_game_ids) - before


def drain_stashed_newly_final_game_ids() -> set[int]:
    """Return and clear stashed newly final game ids."""
    out = set(_stashed_newly_final_game_ids)
    _stashed_newly_final_game_ids.clear()
    return out


def _game_type_label(game: Game) -> str:
    raw = str(game.game_type or "").strip()
    if raw and "playoff" in raw.casefold():
        label = "PO"
    else:
        label = "RS"
    extras: list[str] = []
    if bool(getattr(game, "went_to_shootout", False)):
        extras.append("SO")
    elif bool(getattr(game, "went_to_overtime", False)):
        extras.append("OT")
    if extras:
        return f"{label} · {'/'.join(extras)}"
    return label


def _star_entry(league_session: Session, game: Game, fhm_pid: int | None) -> dict[str, Any] | None:
    if fhm_pid is None:
        return None
    try:
        pid = int(fhm_pid)
    except (TypeError, ValueError):
        return None
    player = league_session.scalars(
        select(Player).where(Player.fhm_player_id == str(pid)).limit(1)
    ).first()
    if player is None:
        return None
    sk = league_session.scalars(
        select(GameSkaterStat)
        .where(
            GameSkaterStat.game_id == int(game.id),
            GameSkaterStat.player_id == int(player.id),
        )
        .limit(1)
    ).first()
    gk = None
    if sk is None:
        gk = league_session.scalars(
            select(GameGoalieStat)
            .where(
                GameGoalieStat.game_id == int(game.id),
                GameGoalieStat.player_id == int(player.id),
            )
            .limit(1)
        ).first()
    tid = sk.team_id if sk is not None else (gk.team_id if gk is not None else None)
    team = league_session.get(Team, tid) if tid is not None else None
    return {
        "name": str(player.full_name or "").strip(),
        "player_id": int(player.id),
        "team_abbr": str(getattr(team, "abbreviation", "") or "").strip() if team else "",
        "fhm_team_id": (
            int(str(team.fhm_team_id).strip())
            if team is not None and str(getattr(team, "fhm_team_id", "") or "").strip().isdigit()
            else None
        ),
    }


def _team_side_fields(team) -> dict[str, Any]:
    """Compact team fields for boxscore payloads (no Flask app context required)."""
    if team is None:
        return {"team_id": None, "abbrev": "", "name": "", "fhm_team_id": None}
    tid = getattr(team, "id", None)
    try:
        team_id = int(tid) if tid is not None else None
    except (TypeError, ValueError):
        team_id = None
    name_fn = getattr(team, "full_display_name", None)
    if callable(name_fn):
        name = str(name_fn() or "").strip()
    else:
        name = str(getattr(team, "name", "") or "").strip()
    abbr = str(getattr(team, "abbreviation", "") or "").strip()
    fhm_raw = getattr(team, "fhm_team_id", None)
    fhm_team_id: int | str | None = None
    if fhm_raw is not None and str(fhm_raw).strip():
        try:
            fhm_team_id = int(str(fhm_raw).strip())
        except ValueError:
            fhm_team_id = str(fhm_raw).strip()
    return {
        "team_id": team_id,
        "abbrev": abbr,
        "name": name,
        "fhm_team_id": fhm_team_id,
    }


def build_game_boxscore_discord_payload(
    league_session: Session,
    *,
    league_slug: str,
    game: Game,
    target_team_id: int,
) -> dict[str, Any]:
    """Compact scoreline + three stars payload for one franchise channel."""
    home = game.home_team
    away = game.away_team
    if home is None and game.home_team_id is not None:
        home = league_session.get(Team, int(game.home_team_id))
    if away is None and game.away_team_id is not None:
        away = league_session.get(Team, int(game.away_team_id))

    home_fields = _team_side_fields(home)
    away_fields = _team_side_fields(away)
    stars = [
        _star_entry(league_session, game, game.fhm_star1_player_id),
        _star_entry(league_session, game, game.fhm_star2_player_id),
        _star_entry(league_session, game, game.fhm_star3_player_id),
    ]
    game_url = build_league_public_url(league_slug, f"/game/{int(game.id)}")
    date_str = game.game_date.isoformat() if game.game_date is not None else ""
    return {
        "title": "Final",
        "game_id": int(game.id),
        "team_id": int(target_team_id),
        "date": date_str,
        "game_type_label": _game_type_label(game),
        "status": str(game.status or "final"),
        "home_score": int(game.home_score or 0),
        "away_score": int(game.away_score or 0),
        "home_team": {
            "team_id": home_fields.get("team_id"),
            "abbrev": home_fields.get("abbrev") or "",
            "name": home_fields.get("name") or "",
            "fhm_team_id": home_fields.get("fhm_team_id"),
        },
        "away_team": {
            "team_id": away_fields.get("team_id"),
            "abbrev": away_fields.get("abbrev") or "",
            "name": away_fields.get("name") or "",
            "fhm_team_id": away_fields.get("fhm_team_id"),
        },
        "stars": [s for s in stars if s],
        "game_url": game_url,
        "url": game_url,
    }


def enqueue_game_boxscore_events_for_game(
    site_session: Session,
    league_session: Session,
    *,
    league_slug: str,
    game_id: int,
) -> int:
    """Enqueue up to two boxscore events (home + away) for one final game."""
    slug = str(league_slug or "").strip()
    if not slug:
        return 0
    if not is_discord_event_route_active(
        site_session, league_slug=slug, event_key=GAME_BOXSCORE_EVENT_KEY
    ):
        return 0
    ensure_game_boxscore_team_channels(site_session, league_session, slug)
    game = league_session.get(Game, int(game_id))
    if game is None or str(game.status or "").lower() != "final":
        return 0
    team_ids = [tid for tid in (game.away_team_id, game.home_team_id) if tid is not None]
    queued = 0
    for tid in team_ids:
        try:
            team_id = int(tid)
        except (TypeError, ValueError):
            continue
        channel_id = resolve_game_boxscore_team_channel_id(
            site_session, league_slug=slug, team_id=team_id
        )
        if not channel_id:
            continue
        payload = build_game_boxscore_discord_payload(
            league_session,
            league_slug=slug,
            game=game,
            target_team_id=team_id,
        )
        row = enqueue_discord_event(
            site_session,
            league_slug=slug,
            event_key=GAME_BOXSCORE_EVENT_KEY,
            payload=payload,
            created_by_user_id=None,
            source_type="game_boxscore",
            source_id=f"{int(game.id)}:{team_id}",
        )
        if row is not None:
            queued += 1
    return queued


def notify_game_boxscores_after_import(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    game_ids: set[int] | list[int] | None = None,
) -> dict[str, int]:
    """Drain stashed finals (plus optional explicit ids) and enqueue boxscore posts."""
    slug = str(league_slug or "").strip()
    ids = set(drain_stashed_newly_final_game_ids())
    if game_ids:
        for gid in game_ids:
            try:
                ids.add(int(gid))
            except (TypeError, ValueError):
                continue
    stats = {"games": 0, "queued": 0, "skipped": 0}
    if not slug or not ids:
        return stats
    ensure_game_boxscore_team_channels(site_session, league_session, slug)
    if not is_discord_event_route_active(
        site_session, league_slug=slug, event_key=GAME_BOXSCORE_EVENT_KEY
    ):
        stats["skipped"] = len(ids)
        return stats
    for gid in sorted(ids):
        stats["games"] += 1
        n = enqueue_game_boxscore_events_for_game(
            site_session,
            league_session,
            league_slug=slug,
            game_id=gid,
        )
        if n:
            stats["queued"] += n
        else:
            stats["skipped"] += 1
    _log.info("Game boxscore Discord after import for %s: %s", slug, stats)
    return stats
