"""Enqueue per-team Discord boxscore posts when games become final on import.

After each FHM import, games that newly become final are queued into both
participating franchise channels (same import-delta cadence as BOWL Six).
Pending finals are persisted until team channel IDs are configured.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, Team
from app.services.discord_events import (
    GAME_BOXSCORE_EVENT_KEY,
    build_league_public_url,
    clear_pending_game_boxscore_ids,
    ensure_game_boxscore_team_channels,
    enqueue_discord_event,
    has_game_boxscore_delivery_target,
    is_discord_event_route_active,
    list_pending_game_boxscore_ids,
    record_pending_game_boxscore_ids,
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


_TOP_SKATERS_PER_TEAM = 3


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


def _fmt_toi(sec: int | None) -> str | None:
    if sec is None:
        return None
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return None
    if s < 0:
        return None
    return f"{s // 60}:{s % 60:02d}"


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pp_fraction(goals: Any, opportunities: Any) -> str | None:
    g = _int_or_none(goals)
    opp = _int_or_none(opportunities)
    if g is None and opp is None:
        return None
    return f"{g or 0}/{opp or 0}"


def _effective_team_shots(
    game: Game, goalie_lines: list[GameGoalieStat]
) -> tuple[int | None, int | None]:
    """Prefer SOG from opposing goalie SA totals when available."""
    sa_by_team: dict[int, int] = {}
    for row in goalie_lines:
        tid = _int_or_none(getattr(row, "team_id", None))
        sa = _int_or_none(getattr(row, "shots_against", None))
        if tid is None or sa is None:
            continue
        sa_by_team[tid] = sa_by_team.get(tid, 0) + sa
    home_id = _int_or_none(getattr(game, "home_team_id", None))
    away_id = _int_or_none(getattr(game, "away_team_id", None))
    home_sog = sa_by_team.get(away_id) if away_id is not None else None
    away_sog = sa_by_team.get(home_id) if home_id is not None else None
    if home_sog is None and away_sog is None:
        return _int_or_none(getattr(game, "home_shots", None)), _int_or_none(
            getattr(game, "away_shots", None)
        )
    return (
        home_sog if home_sog is not None else _int_or_none(getattr(game, "home_shots", None)),
        away_sog if away_sog is not None else _int_or_none(getattr(game, "away_shots", None)),
    )


def _skater_line_label(*, goals: int, assists: int) -> str:
    bits: list[str] = []
    if goals:
        bits.append(f"{goals}G")
    if assists:
        bits.append(f"{assists}A")
    if not bits:
        pts = goals + assists
        return f"{pts}P" if pts else "0P"
    return " ".join(bits)


def _sv_pct(saves: int, shots_against: int) -> float | None:
    if shots_against <= 0:
        return None
    return round(100.0 * float(saves) / float(shots_against), 1)


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
    line = ""
    if sk is not None:
        line = _skater_line_label(
            goals=int(sk.goals or 0),
            assists=int(sk.assists or 0),
        )
    elif gk is not None:
        saves = int(gk.saves or 0)
        sa = int(gk.shots_against or 0)
        pct = _sv_pct(saves, sa)
        line = f"{saves}/{sa}"
        if pct is not None:
            line = f"{line} ({pct:.1f}%)"
    return {
        "name": str(player.full_name or "").strip(),
        "player_id": int(player.id),
        "team_abbr": str(getattr(team, "abbreviation", "") or "").strip() if team else "",
        "line": line,
        "fhm_team_id": (
            int(str(team.fhm_team_id).strip())
            if team is not None and str(getattr(team, "fhm_team_id", "") or "").strip().isdigit()
            else None
        ),
    }


def _load_game_skater_rows(
    league_session: Session, game_id: int
) -> list[GameSkaterStat]:
    return list(
        league_session.scalars(
            select(GameSkaterStat).where(GameSkaterStat.game_id == int(game_id))
        ).all()
    )


def _load_game_goalie_rows(
    league_session: Session, game_id: int
) -> list[GameGoalieStat]:
    return list(
        league_session.scalars(
            select(GameGoalieStat).where(GameGoalieStat.game_id == int(game_id))
        ).all()
    )


def _player_name_map(league_session: Session, player_ids: set[int]) -> dict[int, str]:
    if not player_ids:
        return {}
    rows = league_session.scalars(select(Player).where(Player.id.in_(sorted(player_ids)))).all()
    out: dict[int, str] = {}
    for pl in rows:
        try:
            out[int(pl.id)] = str(pl.full_name or "").strip()
        except (TypeError, ValueError):
            continue
    return out


def _top_skaters_for_team(
    skater_rows: list[GameSkaterStat],
    *,
    team_id: int | None,
    names: dict[int, str],
    limit: int = _TOP_SKATERS_PER_TEAM,
) -> list[dict[str, Any]]:
    if team_id is None:
        return []
    candidates: list[tuple[int, int, float, int, GameSkaterStat]] = []
    for row in skater_rows:
        if _int_or_none(getattr(row, "team_id", None)) != int(team_id):
            continue
        goals = int(getattr(row, "goals", 0) or 0)
        assists = int(getattr(row, "assists", 0) or 0)
        pts = goals + assists
        gr_raw = getattr(row, "game_rating", None)
        try:
            gr = float(gr_raw) if gr_raw is not None else -1.0
        except (TypeError, ValueError):
            gr = -1.0
        pid = _int_or_none(getattr(row, "player_id", None)) or 0
        candidates.append((pts, goals, gr, pid, row))
    candidates.sort(key=lambda t: (-t[0], -t[1], -t[2], t[3]))
    leaders: list[dict[str, Any]] = []
    for pts, goals, _gr, pid, row in candidates:
        if len(leaders) >= limit:
            break
        # Prefer point-getters; allow high-rating fillers only if we have none yet.
        if pts <= 0 and leaders:
            continue
        name = names.get(int(pid), "").strip()
        if not name:
            continue
        assists = int(getattr(row, "assists", 0) or 0)
        leaders.append(
            {
                "name": name,
                "player_id": int(pid),
                "g": goals,
                "a": assists,
                "pts": pts,
                "line": _skater_line_label(goals=goals, assists=assists),
            }
        )
    return leaders


def _goalie_entries_for_team(
    goalie_rows: list[GameGoalieStat],
    *,
    team_id: int | None,
    names: dict[int, str],
) -> list[dict[str, Any]]:
    if team_id is None:
        return []
    entries: list[tuple[int, int, dict[str, Any]]] = []
    for row in goalie_rows:
        if _int_or_none(getattr(row, "team_id", None)) != int(team_id):
            continue
        saves = int(getattr(row, "saves", 0) or 0)
        sa = int(getattr(row, "shots_against", 0) or 0)
        ga = int(getattr(row, "goals_allowed", 0) or 0)
        toi = _int_or_none(getattr(row, "toi_seconds", None)) or 0
        if sa <= 0 and toi <= 0 and saves <= 0 and ga <= 0:
            continue
        pid = _int_or_none(getattr(row, "player_id", None))
        if pid is None:
            continue
        name = names.get(int(pid), "").strip()
        if not name:
            continue
        decision = str(getattr(row, "decision", "") or "").strip().upper() or None
        pct = _sv_pct(saves, sa)
        entries.append(
            (
                toi,
                sa,
                {
                    "name": name,
                    "player_id": int(pid),
                    "saves": saves,
                    "sa": sa,
                    "ga": ga,
                    "sv_pct": pct,
                    "decision": decision,
                    "toi": _fmt_toi(toi if toi > 0 else None),
                    "line": (
                        f"{saves}/{sa}"
                        + (f" ({pct:.1f}%)" if pct is not None else "")
                        + (f" {decision}" if decision else "")
                    ).strip(),
                },
            )
        )
    entries.sort(key=lambda t: (-t[0], -t[1]))
    return [e[2] for e in entries]


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
    """Scoreline + team stats + leaders + goalies + three stars for one franchise channel."""
    home = game.home_team
    away = game.away_team
    if home is None and game.home_team_id is not None:
        home = league_session.get(Team, int(game.home_team_id))
    if away is None and game.away_team_id is not None:
        away = league_session.get(Team, int(game.away_team_id))

    home_fields = _team_side_fields(home)
    away_fields = _team_side_fields(away)
    home_tid = _int_or_none(home_fields.get("team_id")) or _int_or_none(game.home_team_id)
    away_tid = _int_or_none(away_fields.get("team_id")) or _int_or_none(game.away_team_id)

    skater_rows = _load_game_skater_rows(league_session, int(game.id))
    goalie_rows = _load_game_goalie_rows(league_session, int(game.id))
    name_ids = {
        pid
        for pid in (
            *(_int_or_none(getattr(r, "player_id", None)) for r in skater_rows),
            *(_int_or_none(getattr(r, "player_id", None)) for r in goalie_rows),
        )
        if pid is not None
    }
    names = _player_name_map(league_session, name_ids)
    home_shots, away_shots = _effective_team_shots(game, goalie_rows)

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
        "shots": {"away": away_shots, "home": home_shots},
        "special_teams": {
            "away_pp": _pp_fraction(
                getattr(game, "pp_goals_away", None), getattr(game, "pp_opp_away", None)
            ),
            "home_pp": _pp_fraction(
                getattr(game, "pp_goals_home", None), getattr(game, "pp_opp_home", None)
            ),
            "away_pim": _int_or_none(getattr(game, "pim_away", None)),
            "home_pim": _int_or_none(getattr(game, "pim_home", None)),
            "away_hits": _int_or_none(getattr(game, "hits_away", None)),
            "home_hits": _int_or_none(getattr(game, "hits_home", None)),
        },
        "team_leaders": {
            "away": _top_skaters_for_team(skater_rows, team_id=away_tid, names=names),
            "home": _top_skaters_for_team(skater_rows, team_id=home_tid, names=names),
        },
        "goalies": {
            "away": _goalie_entries_for_team(goalie_rows, team_id=away_tid, names=names),
            "home": _goalie_entries_for_team(goalie_rows, team_id=home_tid, names=names),
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
    """Enqueue up to two boxscore events (home + away franchise channels)."""
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
    """Queue boxscores for games that became final since the previous import.

    Newly final ids are persisted so a blank channel config does not drop them;
    once a delivery target exists they enqueue on this or a later import.
    """
    slug = str(league_slug or "").strip()
    ids = set(drain_stashed_newly_final_game_ids())
    if game_ids:
        for gid in game_ids:
            try:
                ids.add(int(gid))
            except (TypeError, ValueError):
                continue
    stats = {"games": 0, "queued": 0, "skipped": 0, "pending": 0}
    if not slug:
        return stats
    if ids:
        record_pending_game_boxscore_ids(site_session, league_slug=slug, game_ids=ids)
    pending = list_pending_game_boxscore_ids(site_session, league_slug=slug)
    stats["pending"] = len(pending)
    if not pending:
        return stats
    ensure_game_boxscore_team_channels(site_session, league_session, slug)
    if not has_game_boxscore_delivery_target(site_session, league_slug=slug):
        # Nothing configured yet — keep pending for the next import after channels are set.
        stats["skipped"] = len(pending)
        _log.info(
            "Game boxscore Discord after import for %s: no channel targets yet; "
            "keeping %s pending final(s)",
            slug,
            len(pending),
        )
        return stats
    if not is_discord_event_route_active(
        site_session, league_slug=slug, event_key=GAME_BOXSCORE_EVENT_KEY
    ):
        stats["skipped"] = len(pending)
        return stats
    cleared: set[int] = set()
    for gid in sorted(pending):
        stats["games"] += 1
        n = enqueue_game_boxscore_events_for_game(
            site_session,
            league_session,
            league_slug=slug,
            game_id=gid,
        )
        if n:
            stats["queued"] += n
            cleared.add(gid)
        else:
            # Missing/non-final game: drop so we do not retry forever.
            game = league_session.get(Game, int(gid))
            if game is None or str(game.status or "").lower() != "final":
                cleared.add(gid)
            stats["skipped"] += 1
    if cleared:
        clear_pending_game_boxscore_ids(site_session, league_slug=slug, game_ids=cleared)
    stats["pending"] = len(pending) - len(cleared)
    _log.info("Game boxscore Discord after import for %s: %s", slug, stats)
    return stats


def recent_final_game_ids_for_boxscores(
    league_session: Session,
    *,
    days: int = 7,
) -> tuple[list[int], date | None, date | None]:
    """Final current-season game ids in the last ``days`` in-game calendar days.

    Window ends at the latest final ``game_date`` and includes ``days`` calendar
    days inclusive (e.g. days=7 → latest through latest-6).
    """
    from app.services.seasons import get_current_season

    try:
        window_days = max(1, int(days))
    except (TypeError, ValueError):
        window_days = 7
    season = get_current_season()
    if season is None:
        return [], None, None
    latest = league_session.scalar(
        select(func.max(Game.game_date)).where(
            Game.season_id == int(season.id),
            Game.status == "final",
            Game.game_date.is_not(None),
        )
    )
    if latest is None:
        return [], None, None
    start = latest - timedelta(days=window_days - 1)
    ids = list(
        league_session.scalars(
            select(Game.id)
            .where(
                Game.season_id == int(season.id),
                Game.status == "final",
                Game.game_date.is_not(None),
                Game.game_date >= start,
                Game.game_date <= latest,
            )
            .order_by(Game.game_date.asc(), Game.id.asc())
        ).all()
    )
    return [int(gid) for gid in ids], start, latest


def queue_recent_game_boxscores(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    days: int = 7,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    """Enqueue franchise boxscores for finals in the last N in-game days."""
    slug = str(league_slug or "").strip()
    try:
        window_days = max(1, int(days))
    except (TypeError, ValueError):
        window_days = 7
    game_ids, start, latest = recent_final_game_ids_for_boxscores(
        league_session, days=window_days
    )
    stats: dict[str, Any] = {
        "games": 0,
        "queued": 0,
        "skipped": 0,
        "days": window_days,
        "window_start": start.isoformat() if start is not None else None,
        "window_end": latest.isoformat() if latest is not None else None,
        "ok": False,
        "message": "",
    }
    if not slug:
        stats["message"] = "Missing league slug."
        return stats
    if not game_ids:
        stats["message"] = "No final games found in that in-game day window."
        stats["ok"] = True
        return stats
    ensure_game_boxscore_team_channels(site_session, league_session, slug)
    if not has_game_boxscore_delivery_target(site_session, league_slug=slug):
        stats["skipped"] = len(game_ids)
        stats["message"] = (
            "No franchise boxscore channel IDs configured. "
            "Enable game_boxscore and paste team channel snowflakes below."
        )
        return stats
    if not is_discord_event_route_active(
        site_session, league_slug=slug, event_key=GAME_BOXSCORE_EVENT_KEY
    ):
        stats["skipped"] = len(game_ids)
        stats["message"] = "game_boxscore route is inactive."
        return stats
    for gid in game_ids:
        stats["games"] += 1
        n = enqueue_game_boxscore_events_for_game(
            site_session,
            league_session,
            league_slug=slug,
            game_id=int(gid),
        )
        if n:
            stats["queued"] += int(n)
        else:
            stats["skipped"] += 1
    _ = created_by_user_id  # reserved for audit callers
    stats["ok"] = True
    stats["message"] = (
        f"Queued {stats['queued']} boxscore event(s) for {stats['games']} final game(s) "
        f"from {stats['window_start']} to {stats['window_end']} "
        f"({stats['days']} in-game day(s))."
    )
    _log.info("Manual game boxscore queue for %s: %s", slug, stats)
    return stats
