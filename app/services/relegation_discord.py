"""Limit BOWL-Relegation Discord outbound events to BLUP / BLOW scope."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Game
from app.services.relegation import is_relegation_league, relegation_main_tier_team_ids

# Built with BLUP/BLOW-only data; do not re-filter here.
_RELEGATION_DISCORD_UNSCOPED_EVENT_KEYS = frozenset(
    {
        "injury_report_delta",
        "sim_cycle_update",
        "gm_export_tracker_poll",
    }
)


def _main_tier_team_ids(session: Session) -> frozenset[int]:
    return relegation_main_tier_team_ids(session)


def relegation_discord_team_id_ok(
    session: Session,
    *,
    league_slug: str,
    team_id: int | None,
) -> bool:
    """True when ``team_id`` is a BLUP/BLOW club (or filtering does not apply)."""
    if not is_relegation_league(league_slug):
        return True
    if team_id is None:
        return True
    main = _main_tier_team_ids(session)
    if not main:
        return True
    try:
        tid = int(team_id)
    except (TypeError, ValueError):
        return False
    return tid in main


def relegation_discord_game_eligible(
    session: Session,
    *,
    league_slug: str,
    game: Game | None,
) -> bool:
    """True when both sides are BLUP/BLOW franchises (or filtering does not apply)."""
    if not is_relegation_league(league_slug):
        return True
    if game is None:
        return False
    main = _main_tier_team_ids(session)
    if not main:
        return True
    for raw_tid in (game.home_team_id, game.away_team_id):
        if raw_tid is None:
            return False
        try:
            tid = int(raw_tid)
        except (TypeError, ValueError):
            return False
        if tid not in main:
            return False
    return True


def relegation_discord_enqueue_allowed(
    session: Session,
    *,
    league_slug: str,
    event_key: str,
    payload: dict[str, Any] | None,
) -> bool:
    """Gate ``enqueue_discord_event`` on bowl-fantasy to BLUP/BLOW-related payloads."""
    slug = str(league_slug or "").strip()
    if not is_relegation_league(slug):
        return True
    key = str(event_key or "").strip()
    p = dict(payload or {})

    if key == "record_broken":
        from app.services.record_broken_discord import record_broken_eligible_for_discord

        return record_broken_eligible_for_discord(
            session, league_slug=slug, payload=p
        )

    if key in _RELEGATION_DISCORD_UNSCOPED_EVENT_KEYS:
        return True

    if key == "game_boxscore":
        gid = p.get("game_id")
        if gid is None:
            return False
        try:
            game_id = int(gid)
        except (TypeError, ValueError):
            return False
        game = session.get(Game, game_id)
        if not relegation_discord_game_eligible(session, league_slug=slug, game=game):
            return False
        target = p.get("team_id")
        if target is not None:
            return relegation_discord_team_id_ok(
                session, league_slug=slug, team_id=int(target)
            )
        return True

    if key == "playoff_bracket_update":
        series = p.get("series")
        if not isinstance(series, list) or not series:
            return False
        main = _main_tier_team_ids(session)
        if not main:
            return True
        for item in series:
            if not isinstance(item, dict):
                continue
            for side_key in ("team_a", "team_b"):
                side = item.get(side_key) or {}
                if not isinstance(side, dict):
                    return False
                raw_tid = side.get("id") or side.get("team_id")
                if raw_tid is None:
                    return False
                if int(raw_tid) not in main:
                    return False
        return True

    tid = p.get("team_id")
    if tid is not None:
        ok = relegation_discord_team_id_ok(
            session, league_slug=slug, team_id=int(tid)
        )
        if not ok:
            return False

    return True


def filter_playoff_bracket_series_for_relegation(
    session: Session,
    *,
    league_slug: str,
    series_rows: list[tuple[Any, dict[str, Any], int | None]],
    teams_by_abbrev: dict[str, Any],
) -> list[tuple[Any, dict[str, Any], int | None]]:
    """Drop playoff series that are not strictly BLUP/BLOW matchups."""
    if not is_relegation_league(league_slug):
        return series_rows
    from app.services.playoff_discord_predictions import _team_side_id_from_json

    main = _main_tier_team_ids(session)
    if not main:
        return series_rows

    def _resolve_tid(side: dict[str, Any] | None) -> int | None:
        if not side:
            return None
        tid = _team_side_id_from_json(side)
        if tid is not None:
            return int(tid)
        abbr = str(side.get("abbreviation") or side.get("abbrev") or "").strip().upper()
        if abbr:
            row = teams_by_abbrev.get(abbr)
            if row is not None:
                return int(row.id)
        return None

    kept: list[tuple[Any, dict[str, Any], int | None]] = []
    for row in series_rows:
        round_label, series, slot_index = row
        ta = series.get("team_a") or {}
        tb = series.get("team_b") or {}
        ta_id = _resolve_tid(ta)
        tb_id = _resolve_tid(tb)
        if ta_id is None or tb_id is None:
            continue
        if int(ta_id) in main and int(tb_id) in main:
            kept.append((round_label, series, slot_index))
    return kept
