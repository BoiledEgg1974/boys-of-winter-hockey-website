"""Fantasy point calculation for BOWL Six from per-game box scores."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, GameGoalieStat, GameSkaterStat, Player, PlayerSkaterStat, ScoringEvent, Season

SLOT_ORDER = ("gk", "def1", "def2", "fwd1", "fwd2", "fwd3")


@dataclass
class PlayerGamePoints:
    player_id: int
    game_id: int
    raw: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)


def position_kind(position: str | None) -> str:
    p = (position or "").strip().upper()
    if p.startswith("G"):
        return "gk"
    if p in ("D", "LD", "RD", "DEF", "DEFENSE"):
        return "def"
    return "fwd"


def slot_accepts_position(slot: str, position: str | None) -> bool:
    kind = position_kind(position)
    if slot == "gk":
        return kind == "gk"
    if slot.startswith("def"):
        return kind == "def"
    if slot.startswith("fwd"):
        return kind == "fwd"
    return False


def _discipline_multiplier(pim_per_gp: float, median_pim: float) -> float:
    if pim_per_gp <= 0 or median_pim <= 0:
        return 1.0
    ratio = pim_per_gp / median_pim
    if ratio >= 2.5:
        return 0.70
    if ratio >= 1.5:
        return 0.85
    return 1.0


def league_median_pim_per_gp(session: Session, season_id: int) -> float:
    rows = session.execute(
        select(PlayerSkaterStat.pim, PlayerSkaterStat.gp).where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == "rs",
            PlayerSkaterStat.gp > 0,
        )
    ).all()
    if not rows:
        return 2.0
    rates = [float(pim or 0) / int(gp) for pim, gp in rows if int(gp or 0) > 0]
    if not rates:
        return 2.0
    rates.sort()
    mid = len(rates) // 2
    return rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2.0


def _gwg_scorers_for_game(session: Session, game: Game) -> set[int]:
    """Return player ids credited with a game-winning goal when inferable."""
    home = int(game.home_score or 0)
    away = int(game.away_score or 0)
    if home == away:
        return set()
    winner_team = game.home_team_id if home > away else game.away_team_id
    loser_final = min(home, away)
    events = list(
        session.scalars(
            select(ScoringEvent)
            .where(ScoringEvent.game_id == game.id, ScoringEvent.scorer_player_id.is_not(None))
            .order_by(ScoringEvent.period.asc(), ScoringEvent.id.asc())
        ).all()
    )
    home_running = 0
    away_running = 0
    gwg: set[int] = set()
    for ev in events:
        if ev.scoring_team_id == game.home_team_id:
            home_running += 1
        elif ev.scoring_team_id == game.away_team_id:
            away_running += 1
        else:
            continue
        if ev.scoring_team_id == winner_team:
            winner_score = home_running if winner_team == game.home_team_id else away_running
            loser_score = away_running if winner_team == game.home_team_id else home_running
            if winner_score > loser_final and loser_score <= loser_final:
                if ev.scorer_player_id:
                    gwg.add(int(ev.scorer_player_id))
    return gwg


def score_skater_line(
    line: GameSkaterStat,
    *,
    discipline: float,
    gwg: bool,
) -> tuple[float, dict[str, float]]:
    br: dict[str, float] = {}
    pos_mult = discipline

    g = int(line.goals or 0)
    a = int(line.assists or 0)
    sog = int(line.shots or 0)
    pm = int(line.plus_minus or 0)
    hits = int(line.hits or 0)
    blocks = int(line.blocked_shots or 0)
    pim = int(line.pim or 0)

    if g:
        br["goals"] = g * 6.0 * pos_mult
    if a:
        br["assists"] = a * 4.0 * pos_mult
    if sog:
        br["shots"] = sog * 0.5 * pos_mult
    if hits:
        br["hits"] = hits * 0.25 * pos_mult
    if blocks:
        br["blocks"] = blocks * 0.35 * pos_mult
    if gwg:
        br["gwg"] = 3.0 * pos_mult
    if pm:
        br["plus_minus"] = float(pm) * 1.0
    if pim:
        br["pim"] = -0.5 * pim

    return sum(br.values()), br


def score_goalie_line(line: GameGoalieStat, game: Game) -> tuple[float, dict[str, float]]:
    br: dict[str, float] = {}
    saves = int(line.saves or 0)
    ga = int(line.goals_allowed or 0)
    decision = (line.decision or "").strip().upper()
    if saves:
        br["saves"] = saves * 0.15
    if ga:
        br["goals_against"] = -1.0 * ga
    if decision == "W":
        br["win"] = 5.0
    if ga == 0 and saves > 0:
        br["shutout"] = 5.0
    return sum(br.values()), br


def player_points_in_games(
    session: Session,
    *,
    season_id: int,
    player_ids: set[int],
    game_ids: list[int],
) -> dict[int, float]:
    """Sum fantasy points per player across the given final games."""
    if not player_ids or not game_ids:
        return {pid: 0.0 for pid in player_ids}

    median_pim = league_median_pim_per_gp(session, season_id)
    pim_gp: dict[int, float] = {}
    for row in session.execute(
        select(PlayerSkaterStat.player_id, PlayerSkaterStat.pim, PlayerSkaterStat.gp).where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == "rs",
            PlayerSkaterStat.player_id.in_(player_ids),
        )
    ).all():
        gp = int(row.gp or 0)
        pim_gp[int(row.player_id)] = float(row.pim or 0) / gp if gp > 0 else 0.0

    games = {
        g.id: g
        for g in session.scalars(select(Game).where(Game.id.in_(game_ids))).all()
    }
    gwg_by_game: dict[int, set[int]] = {}
    for gid, game in games.items():
        gwg_by_game[gid] = _gwg_scorers_for_game(session, game)

    totals: dict[int, float] = {pid: 0.0 for pid in player_ids}

    skater_lines = session.scalars(
        select(GameSkaterStat).where(
            GameSkaterStat.game_id.in_(game_ids),
            GameSkaterStat.player_id.in_(player_ids),
        )
    ).all()
    for line in skater_lines:
        pid = int(line.player_id)
        disc = _discipline_multiplier(pim_gp.get(pid, 0.0), median_pim)
        gwg = pid in gwg_by_game.get(int(line.game_id), set())
        pts, _ = score_skater_line(line, discipline=disc, gwg=gwg)
        totals[pid] = totals.get(pid, 0.0) + pts

    goalie_lines = session.scalars(
        select(GameGoalieStat).where(
            GameGoalieStat.game_id.in_(game_ids),
            GameGoalieStat.player_id.in_(player_ids),
        )
    ).all()
    for line in goalie_lines:
        pid = int(line.player_id)
        game = games.get(int(line.game_id))
        if not game:
            continue
        pts, _ = score_goalie_line(line, game)
        totals[pid] = totals.get(pid, 0.0) + pts

    return totals


def score_lineup_for_slate(
    league_session: Session,
    *,
    season: Season,
    picks: dict[str, int],
    captain_player_id: int | None,
    game_ids: list[int],
) -> tuple[float, dict[str, Any]]:
    """Return total points and JSON-serializable breakdown for one lineup."""
    player_ids = set(picks.values())
    per_player = player_points_in_games(
        league_session, season_id=int(season.id), player_ids=player_ids, game_ids=game_ids
    )
    by_slot: dict[str, dict[str, Any]] = {}
    subtotal = 0.0
    for slot, pid in picks.items():
        pts = float(per_player.get(pid, 0.0))
        by_slot[slot] = {"player_id": pid, "points": round(pts, 2)}
        subtotal += pts
    captain_bonus = 0.0
    if captain_player_id and int(captain_player_id) in player_ids:
        cap_pts = float(per_player.get(int(captain_player_id), 0.0))
        captain_bonus = cap_pts
    total = subtotal + captain_bonus
    payload = {
        "by_slot": by_slot,
        "subtotal": round(subtotal, 2),
        "captain_player_id": captain_player_id,
        "captain_bonus": round(captain_bonus, 2),
        "total": round(total, 2),
        "game_count": len(game_ids),
    }
    return total, payload


def dumps_points(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))
