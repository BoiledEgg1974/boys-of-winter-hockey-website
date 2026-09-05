"""GM achievements: catalog, detectors, post-import evaluation, and page payloads."""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import HOCKEY_LEAGUE_SLUGS
from app.models import (
    DraftPick,
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAllStar,
    HistoryAward,
    HistoryChampion,
    PenaltyEvent,
    Player,
    PlayerContract,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    ScoringEvent,
    Team,
    TeamSeasonRecord,
    TeamStanding,
)
from app.services.playoff_bracket import is_playoff_game_type, is_regular_season_game_type
from app.services.seasons import get_current_season, season_display_label
from app.services.team_records import CHAMPION_RESULT
from app.site_models import (
    GmAchievementUnlock,
    GmAchievementWatermark,
    GmExportAttendance,
    GmLeagueMembership,
    GmTradeProposal,
    User,
)

_log = logging.getLogger(__name__)

ACHIEVEMENT_UNLOCKED_EVENT_KEY = "achievement_unlocked"
ACHIEVEMENT_EXPORT_RECAP_EVENT_KEY = "achievement_export_recap"
REASON_CODE = "gm_achievement"

HOCKEY_SLUGS = frozenset(HOCKEY_LEAGUE_SLUGS)
RELEGATION_ONLY = frozenset({"bowl-fantasy"})
TICKET_CELL_COUNT = 3
TICKET_CELL_P1 = 0.50
TICKET_CELL_P2 = 0.35

_FIGHT_WORDS = ("fight", "fighting", "fisticuffs", "combat")
_MVP_NEEDLES = ("HART", "MOST VALUABLE", "MVP")
_CALDER_NEEDLES = ("CALDER", "ROOKIE OF THE YEAR")
BARGAIN_BIN_SALARY_CAP = 1_000_000
EXPORT_STREAK_TARGET = 10
EXPORT_STREAK_MAX_GAP_DAYS = 8
HOMEGROWN_CORE_TARGET = 8
DRAFT_STEAL_OVERALL = 100
DRAFT_STEAL_POINTS = 70
FIRST_STAR_TARGET = 10
WIN_STREAK_TARGET = 5
OT_WINS_TARGET = 8
NEMESIS_WINS_TARGET = 6
COMEBACK_DEFICIT = 3
FIGHT_NIGHT_MIN = 3
FOUR_GOAL_MIN = 4
BENDER_MIN_GAMES = 4
ELC_GOALS_TARGET = 20
KID_LINE_MAX_AGE = 22
KID_LINE_SCORERS = 3
HOMEGROWN_CUP_TARGET = 12
IRON_DECADE_TARGET = 10
HEIST_POINTS_TARGET = 20
AWARD_SHELF_TARGET = 3
_MAJOR_AWARD_NEEDLES = (
    ("HART", "hart"),
    ("NORRIS", "norris"),
    ("VEZINA", "vezina"),
    ("CALDER", "calder"),
    ("SELKE", "selke"),
)
ACHIEVEMENT_LEAGUE_FIRST_EVENT_KEY = "achievement_league_first"
_CITY_NATION = {
    "toronto": "canada",
    "montreal": "canada",
    "ottawa": "canada",
    "vancouver": "canada",
    "calgary": "canada",
    "edmonton": "canada",
    "winnipeg": "canada",
    "quebec": "canada",
    "hamilton": "canada",
    "chicago": "usa",
    "boston": "usa",
    "new york": "usa",
    "detroit": "usa",
    "philadelphia": "usa",
    "pittsburgh": "usa",
    "washington": "usa",
    "buffalo": "usa",
    "st. louis": "usa",
    "st louis": "usa",
    "dallas": "usa",
    "denver": "usa",
    "colorado": "usa",
    "los angeles": "usa",
    "anaheim": "usa",
    "san jose": "usa",
    "minnesota": "usa",
    "nashville": "usa",
    "tampa": "usa",
    "florida": "usa",
    "carolina": "usa",
    "columbus": "usa",
    "arizona": "usa",
    "phoenix": "usa",
    "seattle": "usa",
    "vegas": "usa",
    "las vegas": "usa",
    "atlanta": "usa",
    "hartford": "usa",
    "new jersey": "usa",
}


@dataclass(frozen=True)
class AchievementDef:
    key: str
    title: str
    description: str
    category: str
    ap: int
    leagues: frozenset[str] | None = None
    hidden: bool = False
    repeatable: bool = False
    repeat_scope: str = "season"
    race: bool = False


CATALOG: tuple[AchievementDef, ...] = (
    AchievementDef("gordie_howe", "Gordie Howe Hat Trick", "One of your players has a goal, assist, and fight in the same game.", "game", 1),
    AchievementDef("all_natural", "All Natural", "One of your players scores a natural hat trick.", "game", 1),
    AchievementDef("goalie_win", "A Goalie Win", "Win a game 1–0 despite giving up 40 or more shots.", "game", 1),
    AchievementDef("nationalism", "Nationalism", "Play a game with a lineup composed entirely of players from the nation your team is based in.", "game", 2),
    AchievementDef("make_playoffs", "Kiss from a Rose", "Make the playoffs.", "playoffs", 1, repeatable=True),
    AchievementDef("senior_team", "The Senior Team", "Have at least 3 players on your roster over the age of 40.", "season", 1, repeatable=True),
    AchievementDef("game54", "game54", "Draft a player with the 54th overall pick.", "career", 1),
    AchievementDef("hometown_roster", "Hometown Roster", "Have at least three players on your roster born in your team's city.", "season", 2, repeatable=True),
    AchievementDef("upset", "Upset!", "Win a playoff series against a team that finished ahead of you in the regular season.", "playoffs", 2),
    AchievementDef("guarantee", "The Guarantee", "Win a 7-game playoff series after being down 3–2.", "playoffs", 2),
    AchievementDef("road_warrior", "Road Warrior", "Lead the league in road wins.", "season", 2, repeatable=True),
    AchievementDef("youre_special", "You're Special", "Finish first in the league in both power play and penalty killing percentage.", "season", 2, repeatable=True),
    AchievementDef("new_team_mvp", "New Team, Who's This?", "Have a player win the league MVP in their first season on your team.", "season", 2, repeatable=True),
    AchievementDef("pinnacle", "The Pinnacle", "Win the BOWL Cup.", "playoffs", 3),
    AchievementDef("rocket", "Outracing the Rocket", "One of your players scores 50 goals in 50 or fewer games.", "season", 3, repeatable=True),
    AchievementDef("zero_to_hero", "Zero to Hero", "Win the championship after finishing last overall the previous season.", "playoffs", 3),
    AchievementDef("going_up", "Going Up", "Your team is promoted to a higher league.", "career", 3, RELEGATION_ONLY),
    AchievementDef("presidents", "Presidents' Club", "Finish first in the league in the regular season.", "season", 3, repeatable=True),
    AchievementDef("playoff_sweep", "Playoff Sweep", "Sweep a playoff series.", "playoffs", 3),
    AchievementDef("century_club", "100-Point Skater", "One of your players records 100 points in a season.", "season", 3, repeatable=True),
    AchievementDef("fifty_goals", "50-Goal Skater", "One of your players scores 50 goals in a season.", "season", 3, repeatable=True),
    AchievementDef("forty_win_goalie", "40-Win Goalie", "One of your goalies wins 40 games in a season.", "season", 3, repeatable=True),
    AchievementDef("dynasty", "A Real Dynasty", "Win the league championship five consecutive times.", "career", 5),
    AchievementDef("two_hundred", "The 200 Club", "One of your players scores 200 points in a season.", "season", 5),
    AchievementDef("true_franchise", "True Franchise Manager", "Manage a single team for ten seasons.", "career", 5),
    AchievementDef("cup_eight_seed", "Cup as 8-seed", "Win the BOWL Cup as the lowest playoff seed.", "playoffs", 5),
    AchievementDef("comeback_kids", "Comeback Kids", "Win a game after trailing by 3 or more goals.", "game", 2),
    AchievementDef("four_goal_night", "Four-Goal Night", "One of your players scores 4 or more goals in a game.", "game", 2),
    AchievementDef("fight_night", "Fight Night", "Three or more of your players take fighting majors in the same game.", "game", 1),
    AchievementDef(
        "league_first_hat",
        "League First: Hat Trick",
        "Score the first hat trick of the season.",
        "game",
        2,
        repeatable=True,
        race=True,
    ),
    AchievementDef("statement_win", "Statement Win", "Beat the first-place team while sitting outside a playoff spot.", "game", 2),
    AchievementDef("on_a_heater", "On a Heater", "Win 5 regular-season games in a row.", "season", 1, repeatable=True),
    AchievementDef("home_cooking", "Home Cooking", "Lead the league in home wins.", "season", 2, repeatable=True),
    AchievementDef("overtime_merchant", "Overtime Merchant", "Win 8 or more overtime or shootout games in a season.", "season", 2, repeatable=True),
    AchievementDef("three_star_season", "Three-Star Season", "One of your players is named first star 10 times in a season.", "season", 2, repeatable=True),
    AchievementDef("bargain_bin", "Bargain Bin", "A player making under $1M AAV scores 30 or more goals.", "season", 2, repeatable=True),
    AchievementDef("calder_club", "Calder Club", "One of your players wins the Calder Trophy.", "season", 2, repeatable=True),
    AchievementDef("nemesis", "Nemesis", "Beat the same opponent 6 or more times in one season.", "season", 2, repeatable=True),
    AchievementDef("playoff_ot_hero", "Playoff OT Hero", "Score the overtime winner in a playoff game.", "playoffs", 2),
    AchievementDef(
        "reverse_sweep",
        "Reverse Sweep",
        "Win a playoff series after trailing 0–3.",
        "playoffs",
        5,
        hidden=True,
    ),
    AchievementDef(
        "homegrown_core",
        "Homegrown Core",
        "Have at least 8 players on your roster who were drafted by your franchise.",
        "career",
        3,
    ),
    AchievementDef(
        "draft_steal",
        "Draft Steal",
        "A player you drafted 100th overall or later records 70 points in a season or makes an all-star team.",
        "career",
        3,
    ),
    AchievementDef("export_streak", "Export Streak", "Record 10 consecutive scheduled exports.", "career", 1),
    AchievementDef(
        "league_first_shutout",
        "League First: Shutout",
        "Record the first shutout of the season.",
        "game",
        2,
        repeatable=True,
        race=True,
    ),
    AchievementDef(
        "league_first_four",
        "League First: Four-Goal Night",
        "Score the first 4-goal game of the season.",
        "game",
        2,
        repeatable=True,
        race=True,
    ),
    AchievementDef(
        "special_teams_season",
        "Special Teams Season",
        "Finish in the top 3 in both power play and penalty killing percentage.",
        "season",
        2,
        repeatable=True,
    ),
    AchievementDef(
        "the_bender",
        "The Bender",
        "Go undefeated in a calendar month of regular-season games (minimum 4). Awarded when that month’s schedule is complete.",
        "season",
        2,
        repeatable=True,
        repeat_scope="month",
    ),
    AchievementDef(
        "elc_lightning",
        "ELC Lightning",
        "An entry-level contract player scores 20 or more goals.",
        "season",
        2,
        repeatable=True,
    ),
    AchievementDef(
        "kid_line_energy",
        "Kid Line Energy",
        "Three players aged 22 or younger each score in the same game.",
        "game",
        2,
    ),
    AchievementDef(
        "perfect_attendance",
        "Perfect Attendance",
        "Hit every league export in a 45-day window.",
        "career",
        2,
        repeatable=True,
    ),
    AchievementDef(
        "guarantee_remixed",
        "The Guarantee, Remixed",
        "Win a playoff game on the road after losing the first two games of the series.",
        "playoffs",
        2,
    ),
    AchievementDef(
        "swept_not_forgotten",
        "Swept, Not Forgotten",
        "Get swept in a playoff series.",
        "playoffs",
        1,
    ),
    AchievementDef(
        "playoff_shutout_pair",
        "Back-to-Back Playoff Shutouts",
        "Your goalie records consecutive playoff shutouts.",
        "playoffs",
        4,
    ),
    AchievementDef(
        "award_shelf",
        "Award Shelf",
        "Win three different major player awards in the same season (Hart, Norris, Vezina, Calder, or Selke).",
        "season",
        5,
        hidden=True,
    ),
    AchievementDef(
        "homegrown_cup",
        "Homegrown Cup",
        "Win the BOWL Cup with at least 12 self-drafted players in the playoff lineup.",
        "playoffs",
        5,
    ),
    AchievementDef(
        "iron_decade",
        "Iron Decade",
        "Make the playoffs in 10 consecutive seasons.",
        "career",
        4,
    ),
    AchievementDef(
        "the_heist",
        "The Heist",
        "A player you acquired via the Trade Tool records 20 or more points for you that season.",
        "season",
        3,
        repeatable=True,
    ),
)

CATALOG_BY_KEY = {item.key: item for item in CATALOG}


@dataclass
class PlayoffSeries:
    team_a: int
    team_b: int
    wins_a: int
    wins_b: int
    winner_id: int | None
    game_winners: list[int]
    rank_a: int | None
    rank_b: int | None
    games: list[Any]

    @property
    def is_complete(self) -> bool:
        return (self.wins_a >= 4) or (self.wins_b >= 4)

    @property
    def is_sweep(self) -> bool:
        return (self.wins_a == 4 and self.wins_b == 0) or (self.wins_b == 4 and self.wins_a == 0)

    def trailed_3_2_then_won(self) -> bool:
        if self.winner_id is None or len(self.game_winners) < 7:
            return False
        wa = wb = 0
        after_five_down = False
        for i, wid in enumerate(self.game_winners, start=1):
            if wid == self.team_a:
                wa += 1
            elif wid == self.team_b:
                wb += 1
            if i == 5:
                if self.winner_id == self.team_a and wa == 2 and wb == 3:
                    after_five_down = True
                if self.winner_id == self.team_b and wb == 2 and wa == 3:
                    after_five_down = True
        return after_five_down and self.wins_a + self.wins_b == 7

    def trailed_0_3_then_won(self) -> bool:
        if self.winner_id is None or len(self.game_winners) < 7:
            return False
        if self.wins_a + self.wins_b != 7:
            return False
        wa = wb = 0
        down_0_3 = False
        for i, wid in enumerate(self.game_winners, start=1):
            if wid == self.team_a:
                wa += 1
            elif wid == self.team_b:
                wb += 1
            if i == 3:
                if self.winner_id == self.team_a and wa == 0 and wb == 3:
                    down_0_3 = True
                if self.winner_id == self.team_b and wb == 0 and wa == 3:
                    down_0_3 = True
        return down_0_3

    def is_upset(self) -> bool:
        if self.winner_id is None or self.rank_a is None or self.rank_b is None:
            return False
        if self.winner_id == self.team_a:
            return self.rank_a > self.rank_b
        if self.winner_id == self.team_b:
            return self.rank_b > self.rank_a
        return False

    def winner_is_lowest_seed(self, playoff_team_ranks: dict[int, int]) -> bool:
        if self.winner_id is None or not playoff_team_ranks:
            return False
        worst = max(playoff_team_ranks.values())
        return playoff_team_ranks.get(self.winner_id) == worst


def catalog_for_league(league_slug: str) -> list[AchievementDef]:
    slug = (league_slug or "").strip()
    out: list[AchievementDef] = []
    for item in CATALOG:
        if item.leagues is None or slug in item.leagues:
            out.append(item)
    return out


def unlock_source_ref(league_slug: str, team_id: int, key: str) -> str:
    return f"gm_ach:{league_slug}:{int(team_id)}:{key}"


def roll_reward_cell(rng: random.Random | None = None) -> int:
    """Roll one scratch spot: 1 (50%), 2 (35%), or 3 (15%)."""
    dice = rng or random.Random()
    roll = dice.random()
    if roll < TICKET_CELL_P1:
        return 1
    if roll < TICKET_CELL_P1 + TICKET_CELL_P2:
        return 2
    return 3


def roll_reward_cells(rng: random.Random | None = None) -> list[int]:
    dice = rng or random.Random()
    return [roll_reward_cell(dice) for _ in range(TICKET_CELL_COUNT)]


def parse_reward_cells(raw: Any) -> list[int] | None:
    values: Any = raw
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(values, list) or len(values) != TICKET_CELL_COUNT:
        return None
    cells: list[int] = []
    for item in values:
        try:
            n = int(item)
        except (TypeError, ValueError):
            return None
        if n not in (1, 2, 3):
            return None
        cells.append(n)
    return cells


def reward_payload(unlock: GmAchievementUnlock, spec: AchievementDef | None = None) -> dict[str, Any]:
    cells = parse_reward_cells(unlock.reward_cells_json)
    ticket_ap = int(unlock.reward_ticket_ap or 0) if unlock.reward_ticket_ap is not None else (sum(cells) if cells else None)
    multiplier = int(unlock.reward_multiplier or 0) if unlock.reward_multiplier is not None else (int(spec.ap) if spec else None)
    claimed = unlock.claimed_at is not None
    total = int(unlock.ap_delta or 0) if claimed else None
    if total is None and cells is not None and multiplier:
        total = int(ticket_ap or 0) * int(multiplier)
    return {
        "storage_key": str(unlock.achievement_key),
        "title": spec.title if spec else str(unlock.achievement_key),
        "cells": cells,
        "ticket_ap": ticket_ap,
        "multiplier": multiplier,
        "total_ap": total if claimed else None,
        "claimed": claimed,
        "claimed_at": unlock.claimed_at.isoformat() if unlock.claimed_at else None,
        "unlocked_at": unlock.unlocked_at.isoformat() if unlock.unlocked_at else None,
    }


def _find_team_unlock(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    storage_key: str,
) -> GmAchievementUnlock | None:
    return session.scalar(
        select(GmAchievementUnlock).where(
            GmAchievementUnlock.league_slug == league_slug,
            GmAchievementUnlock.team_id == int(team_id),
            GmAchievementUnlock.achievement_key == storage_key,
        ).limit(1)
    )


def start_achievement_scratch(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    storage_key: str,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Lock in the three ticket cells. Idempotent once rolled."""
    unlock = _find_team_unlock(session, league_slug=league_slug, team_id=team_id, storage_key=storage_key)
    if unlock is None:
        return {"ok": False, "error": "Achievement is not unlocked."}
    spec = CATALOG_BY_KEY.get(catalog_key_from_storage(unlock.achievement_key))
    if spec is None:
        return {"ok": False, "error": "Unknown achievement."}
    cells = parse_reward_cells(unlock.reward_cells_json)
    if cells is None:
        cells = roll_reward_cells(rng)
        unlock.reward_cells_json = json.dumps(cells)
        unlock.reward_ticket_ap = sum(cells)
        unlock.reward_multiplier = int(spec.ap)
    elif unlock.reward_ticket_ap is None or unlock.reward_multiplier is None:
        unlock.reward_ticket_ap = sum(cells)
        unlock.reward_multiplier = int(spec.ap)
    payload = reward_payload(unlock, spec)
    payload["ok"] = True
    payload["claimable"] = unlock.claimed_at is None
    return payload


def claim_achievement_scratch(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    storage_key: str,
    created_by_user_id: int | None,
) -> dict[str, Any]:
    """Credit ticket sum × multiplier once. Idempotent after the first claim."""
    unlock = _find_team_unlock(session, league_slug=league_slug, team_id=team_id, storage_key=storage_key)
    if unlock is None:
        return {"ok": False, "error": "Achievement is not unlocked."}
    spec = CATALOG_BY_KEY.get(catalog_key_from_storage(unlock.achievement_key))
    if spec is None:
        return {"ok": False, "error": "Unknown achievement."}
    cells = parse_reward_cells(unlock.reward_cells_json)
    if cells is None:
        return {"ok": False, "error": "Scratch the ticket first."}
    ticket_ap = int(unlock.reward_ticket_ap or sum(cells))
    multiplier = int(unlock.reward_multiplier or spec.ap)
    total = ticket_ap * multiplier
    if unlock.claimed_at is None:
        unlock.reward_ticket_ap = ticket_ap
        unlock.reward_multiplier = multiplier
        unlock.ap_delta = total
        unlock.claimed_at = datetime.utcnow()
        credit_achievement_ap(
            league_slug=league_slug,
            team_id=int(team_id),
            spec=spec,
            source_ref=str(unlock.source_ref),
            created_by_user_id=created_by_user_id,
            storage_key=str(unlock.achievement_key),
            delta=total,
        )
    from app.services.ap_service import team_ap_balance

    payload = reward_payload(unlock, spec)
    payload["ok"] = True
    payload["claimable"] = False
    payload["total_ap"] = int(unlock.ap_delta or 0)
    payload["balance"] = int(team_ap_balance(league_slug, int(team_id)))
    return payload


def unclaimed_unlock_count(session: Session, league_slug: str, team_id: int) -> int:
    return int(
        session.scalar(
            select(func.count(GmAchievementUnlock.id)).where(
                GmAchievementUnlock.league_slug == league_slug,
                GmAchievementUnlock.team_id == int(team_id),
                GmAchievementUnlock.claimed_at.is_(None),
            )
        )
        or 0
    )


def mark_heritage_claimed_unlocks(session: Session, league_slug: str) -> int:
    """Treat pre-scratch unlocks that already have AP as claimed."""
    from app.site_models import ApLedgerEntry

    marked = 0
    unlocks = list(
        session.scalars(
            select(GmAchievementUnlock).where(
                GmAchievementUnlock.league_slug == league_slug,
                GmAchievementUnlock.claimed_at.is_(None),
            )
        ).all()
    )
    if not unlocks:
        return 0
    refs = [str(row.source_ref) for row in unlocks if row.source_ref]
    existing_refs: set[str] = set()
    if refs:
        existing_refs = {
            str(ref)
            for ref in session.scalars(
                select(ApLedgerEntry.source_ref).where(ApLedgerEntry.source_ref.in_(refs))
            ).all()
            if ref
        }
    now = datetime.utcnow()
    for unlock in unlocks:
        has_ledger = str(unlock.source_ref) in existing_refs
        if int(unlock.ap_delta or 0) > 0 or has_ledger:
            unlock.claimed_at = unlock.unlocked_at or now
            if unlock.reward_multiplier is None:
                spec = CATALOG_BY_KEY.get(catalog_key_from_storage(unlock.achievement_key))
                if spec is not None:
                    unlock.reward_multiplier = int(spec.ap)
            marked += 1
    return marked


def catalog_key_from_storage(key: str) -> str:
    raw = str(key or "")
    if raw in CATALOG_BY_KEY:
        return raw
    base = raw.split(":", 1)[0]
    return base if base in CATALOG_BY_KEY else raw


def storage_key_for(
    spec: AchievementDef,
    season_label: str,
    period: str | None = None,
) -> str:
    if spec.repeat_scope == "month" and period:
        return f"{spec.key}:{period}"
    if spec.repeatable and season_label:
        return f"{spec.key}:{season_label}"
    return spec.key


def expand_legacy_pairs(pairs: set[tuple[int, str]], season_label: str) -> set[tuple[int, str]]:
    """Treat unsuffixed repeatable keys as already earned for the current season."""
    out = set(pairs)
    label = (season_label or "").strip()
    if not label:
        return out
    for tid, key in pairs:
        spec = CATALOG_BY_KEY.get(key)
        if spec and spec.repeatable:
            out.add((int(tid), storage_key_for(spec, label)))
    return out


def player_ids_from_drag_keys(keys: Iterable[str]) -> list[int]:
    out: list[int] = []
    for raw in keys:
        text = str(raw or "").strip()
        if not text.startswith("player:"):
            continue
        rest = text.split(":", 1)[1]
        if rest.isdigit():
            out.append(int(rest))
    return out


def acquired_by_team_from_ledger(
    from_team_id: int,
    to_team_id: int,
    left_out: Iterable[str],
    right_out: Iterable[str],
) -> dict[int, set[int]]:
    """Left assets go to ``to_team``; right assets go to ``from_team``."""
    acquired: dict[int, set[int]] = {}
    for pid in player_ids_from_drag_keys(left_out):
        acquired.setdefault(int(to_team_id), set()).add(pid)
    for pid in player_ids_from_drag_keys(right_out):
        acquired.setdefault(int(from_team_id), set()).add(pid)
    return acquired


def detect_heist(
    acquired: dict[int, set[int]],
    productions: Iterable[tuple[int, int, int]],
    *,
    target: int = HEIST_POINTS_TARGET,
) -> list[tuple[int, int, int]]:
    """Return (team_id, player_id, points) when an acquired player hits the bar."""
    hits: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for team_id, player_id, points in productions:
        tid = int(team_id)
        pid = int(player_id)
        pts = int(points or 0)
        if pts < int(target) or pid not in acquired.get(tid, set()):
            continue
        pair = (tid, pid)
        if pair in seen:
            continue
        seen.add(pair)
        hits.append((tid, pid, pts))
    return hits


def place_label(n: int | None) -> str:
    if n is None or int(n) <= 0:
        return ""
    value = int(n)
    if 10 <= (value % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def format_export_recap(*, titles: list[str], rank: int | None) -> tuple[str, str]:
    names = [str(t).strip() for t in titles if str(t).strip()]
    if not names:
        unlocked = "You unlocked new achievements"
    elif len(names) == 1:
        unlocked = f"You unlocked {names[0]}"
    elif len(names) == 2:
        unlocked = f"You unlocked {names[0]} and {names[1]}"
    else:
        unlocked = f"You unlocked {', '.join(names[:-1])}, and {names[-1]}"
    if rank:
        body = f"{unlocked}. Scratch the ticket on Achievements to claim your AP. You're #{int(rank)} on the trophy board."
    else:
        body = f"{unlocked}. Scratch the ticket on Achievements to claim your AP."
    return "Export recap", body


def rewrite_truths_to_storage(
    truths: dict[int, dict[str, dict[str, Any]]],
    season_label: str,
) -> dict[int, dict[str, dict[str, Any]]]:
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for team_id, keys in truths.items():
        bucket = out.setdefault(int(team_id), {})
        for key, meta in keys.items():
            base = catalog_key_from_storage(key)
            spec = CATALOG_BY_KEY.get(base)
            if spec is None:
                continue
            period = str((meta or {}).get("period") or "") or None
            store = key if catalog_key_from_storage(key) != key else storage_key_for(spec, season_label, period)
            if store not in bucket:
                bucket[store] = dict(meta or {})
    return out


def major_award_slot(name: str | None) -> str | None:
    up = " ".join((name or "").upper().split())
    for needle, slot in _MAJOR_AWARD_NEEDLES:
        if needle in up:
            return slot
    return None


def detect_road_win_after_dropping_first_two(games: Iterable[Any], team_id: int) -> bool:
    decided: list[Any] = []
    for game in games:
        winner, loser = _game_winner_loser(game)
        if winner is None or loser is None:
            continue
        decided.append(game)
    if len(decided) < 3:
        return False
    first_two = [_game_winner_loser(g)[1] for g in decided[:2]]
    if any(loser != int(team_id) for loser in first_two):
        return False
    for game in decided[2:]:
        winner, _loser = _game_winner_loser(game)
        if winner == int(team_id) and int(getattr(game, "away_team_id", 0) or 0) == int(team_id):
            return True
    return False


def detect_consecutive_playoff_shutouts(goalie_rows: list[tuple[int, int, int]]) -> bool:
    """Rows are (order, player_id, goals_allowed) for one team, chronological."""
    by_player: dict[int, list[int]] = {}
    for _order, pid, ga in goalie_rows:
        by_player.setdefault(int(pid), []).append(int(ga or 0))
    for gas in by_player.values():
        for a, b in zip(gas, gas[1:]):
            if a == 0 and b == 0:
                return True
    return False


def month_undefeated(results: Iterable[str], *, min_games: int = BENDER_MIN_GAMES) -> bool:
    letters = [r for r in results if r]
    if len(letters) < int(min_games):
        return False
    return all(r != "L" for r in letters) and any(r == "W" for r in letters)


def regular_season_month_is_complete(
    schedule: Iterable[Any],
    period: str,
    *,
    as_of_game_date: date | None = None,
) -> bool:
    """True when every regular-season game in ``YYYY-MM`` is final (and none remain after as-of)."""
    month_games = [
        g
        for g in schedule
        if getattr(g, "game_date", None)
        and g.game_date.strftime("%Y-%m") == period
        and is_regular_season_game_type(getattr(g, "game_type", None))
    ]
    if not month_games:
        return False
    if as_of_game_date is not None and any(g.game_date > as_of_game_date for g in month_games):
        return False
    visible = [
        g for g in month_games if as_of_game_date is None or g.game_date <= as_of_game_date
    ]
    return bool(visible) and all(str(getattr(g, "status", "") or "") == "final" for g in visible)


def player_age_on(birth: date | None, on_date: date) -> int | None:
    if birth is None:
        return None
    years = on_date.year - birth.year
    if (on_date.month, on_date.day) < (birth.month, birth.day):
        years -= 1
    return years


def is_fighting_infraction(text: str | None) -> bool:
    t = (text or "").strip().lower()
    return any(word in t for word in _FIGHT_WORDS)


def detect_gordie_howe(*, goals: int, assists: int, fought: bool) -> bool:
    return int(goals or 0) >= 1 and int(assists or 0) >= 1 and bool(fought)


def _event_sort_key(period: int | None, time_elapsed: str | None) -> tuple[int, int, int]:
    raw = (time_elapsed or "0:00").replace(".", ":")
    parts = raw.split(":")
    try:
        mins = int(parts[0])
        secs = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        mins, secs = 0, 0
    return (int(period or 1), mins, secs)


def detect_natural_hat_trick(events: Iterable[Any]) -> int | None:
    """Return scorer player_id if three consecutive goals (no one else scores) belong to one player."""
    ordered = sorted(
        [e for e in events if getattr(e, "scorer_player_id", None)],
        key=lambda e: _event_sort_key(getattr(e, "period", None), getattr(e, "time_elapsed", None)),
    )
    run_pid: int | None = None
    run = 0
    for ev in ordered:
        try:
            pid = int(ev.scorer_player_id)
        except (TypeError, ValueError):
            run_pid = None
            run = 0
            continue
        if pid == run_pid:
            run += 1
        else:
            run_pid = pid
            run = 1
        if run >= 3:
            return pid
    return None


def _game_winner_loser(game: Any) -> tuple[int | None, int | None]:
    hs = getattr(game, "home_score", None)
    aws = getattr(game, "away_score", None)
    if hs is None or aws is None:
        return None, None
    if int(hs) == int(aws):
        return None, None
    hid = getattr(game, "home_team_id", None)
    aid = getattr(game, "away_team_id", None)
    if hid is None or aid is None:
        return None, None
    if int(hs) > int(aws):
        return int(hid), int(aid)
    return int(aid), int(hid)


def detect_comeback_from_events(
    events: Iterable[Any],
    *,
    home_team_id: int | None,
    away_team_id: int | None,
    winner_id: int | None,
    deficit: int = COMEBACK_DEFICIT,
) -> bool:
    if not winner_id or not home_team_id or not away_team_id:
        return False
    home = away = 0
    max_down = 0
    ordered = sorted(
        [e for e in events if getattr(e, "scoring_team_id", None)],
        key=lambda e: _event_sort_key(getattr(e, "period", None), getattr(e, "time_elapsed", None)),
    )
    if not ordered:
        return False
    hid, aid, wid = int(home_team_id), int(away_team_id), int(winner_id)
    for ev in ordered:
        try:
            tid = int(ev.scoring_team_id)
        except (TypeError, ValueError):
            continue
        if tid == hid:
            home += 1
        elif tid == aid:
            away += 1
        if wid == hid:
            max_down = max(max_down, away - home)
        elif wid == aid:
            max_down = max(max_down, home - away)
    return max_down >= int(deficit)


def detect_comeback_from_period_scores(
    game: Any,
    *,
    winner_id: int | None,
    deficit: int = COMEBACK_DEFICIT,
) -> bool:
    if not winner_id:
        return False
    hid = getattr(game, "home_team_id", None)
    aid = getattr(game, "away_team_id", None)
    if hid is None or aid is None:
        return False
    home_periods = (
        getattr(game, "score_home_p1", None),
        getattr(game, "score_home_p2", None),
        getattr(game, "score_home_p3", None),
        getattr(game, "score_home_ot", None),
    )
    away_periods = (
        getattr(game, "score_away_p1", None),
        getattr(game, "score_away_p2", None),
        getattr(game, "score_away_p3", None),
        getattr(game, "score_away_ot", None),
    )
    if all(v is None for v in home_periods) and all(v is None for v in away_periods):
        return False
    home = away = 0
    max_down = 0
    wid = int(winner_id)
    for hp, ap in zip(home_periods, away_periods):
        home += int(hp or 0)
        away += int(ap or 0)
        if wid == int(hid):
            max_down = max(max_down, away - home)
        elif wid == int(aid):
            max_down = max(max_down, home - away)
    return max_down >= int(deficit)


def detect_playoff_ot_winner(
    events: Iterable[Any],
    game: Any,
) -> tuple[int | None, int | None]:
    """Return (scorer_id, scoring_team_id) for a playoff overtime winner."""
    if not is_playoff_game_type(getattr(game, "game_type", None)):
        return None, None
    if not getattr(game, "went_to_overtime", False):
        return None, None
    ordered = sorted(
        [e for e in events if getattr(e, "scorer_player_id", None)],
        key=lambda e: _event_sort_key(getattr(e, "period", None), getattr(e, "time_elapsed", None)),
    )
    ot_events = [e for e in ordered if int(getattr(e, "period", 0) or 0) >= 4]
    last = (ot_events or ordered)[-1] if (ot_events or ordered) else None
    if last is None:
        return None, None
    try:
        pid = int(last.scorer_player_id)
    except (TypeError, ValueError):
        pid = None
    try:
        tid = int(last.scoring_team_id) if last.scoring_team_id else None
    except (TypeError, ValueError):
        tid = None
    return pid, tid


def max_win_streak(results: Iterable[str]) -> int:
    best = cur = 0
    for result in results:
        if result == "W":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def game_outcome_letter(game: Any, team_id: int) -> str | None:
    winner, loser = _game_winner_loser(game)
    if winner is None:
        hs = getattr(game, "home_score", None)
        aws = getattr(game, "away_score", None)
        if hs is not None and aws is not None and int(hs) == int(aws):
            return "T"
        return None
    if int(team_id) == winner:
        return "W"
    if int(team_id) == loser:
        return "L"
    return None


def export_streak_len(dates: Iterable[date], *, max_gap_days: int = EXPORT_STREAK_MAX_GAP_DAYS) -> int:
    ordered = sorted({d for d in dates if d is not None})
    if not ordered:
        return 0
    best = cur = 1
    for prev, nxt in zip(ordered, ordered[1:]):
        gap = (nxt - prev).days
        if 1 <= gap <= int(max_gap_days):
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def playoff_spot_cutoff(team_count: int) -> int:
    n = int(team_count or 0)
    if n >= 24:
        return 16
    if n >= 12:
        return 8
    return max(2, n // 2)


def is_calder_award(name: str | None) -> bool:
    up = " ".join((name or "").upper().split())
    return any(n in up for n in _CALDER_NEEDLES)


def detect_goalie_win_1_0_40(
    *,
    home_score: int | None,
    away_score: int | None,
    home_shots: int | None,
    away_shots: int | None,
    team_is_home: bool,
) -> bool:
    if home_score is None or away_score is None:
        return False
    if team_is_home:
        return int(home_score) == 1 and int(away_score) == 0 and int(away_shots or 0) >= 40
    return int(away_score) == 1 and int(home_score) == 0 and int(home_shots or 0) >= 40


def build_playoff_series(
    games: list[Any],
    rs_rank: dict[int, int],
) -> list[PlayoffSeries]:
    """Group playoff games into best-of-7 pairings (pair of team ids)."""
    buckets: dict[frozenset[int], list[Any]] = {}
    for g in games:
        hid = getattr(g, "home_team_id", None)
        aid = getattr(g, "away_team_id", None)
        if hid is None or aid is None:
            continue
        buckets.setdefault(frozenset({int(hid), int(aid)}), []).append(g)

    series: list[PlayoffSeries] = []
    for pair, rows in buckets.items():
        if len(pair) != 2:
            continue
        team_a, team_b = sorted(pair)
        ordered = sorted(
            rows,
            key=lambda g: (getattr(g, "game_date", None) or date.min, int(getattr(g, "id", 0) or 0)),
        )
        wa = wb = 0
        winners: list[int] = []
        for g in ordered:
            hs = getattr(g, "home_score", None)
            aws = getattr(g, "away_score", None)
            if hs is None or aws is None:
                continue
            if int(hs) == int(aws):
                continue
            winner = int(g.home_team_id) if int(hs) > int(aws) else int(g.away_team_id)
            winners.append(winner)
            if winner == team_a:
                wa += 1
            else:
                wb += 1
            if wa >= 4 or wb >= 4:
                break
        winner_id = team_a if wa >= 4 else team_b if wb >= 4 else None
        series.append(
            PlayoffSeries(
                team_a=team_a,
                team_b=team_b,
                wins_a=wa,
                wins_b=wb,
                winner_id=winner_id,
                game_winners=winners,
                rank_a=rs_rank.get(team_a),
                rank_b=rs_rank.get(team_b),
                games=ordered,
            )
        )
    return series


def _norm_city(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower()).strip()


def _cities_match(a: str | None, b: str | None) -> bool:
    na, nb = _norm_city(a), _norm_city(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _nation_for_team(team: Team | None) -> str | None:
    if team is None:
        return None
    blob = " ".join(filter(None, [team.city, team.name, team.nickname])).lower()
    for needle, nation in _CITY_NATION.items():
        if needle in blob:
            return nation
    return None


def _norm_nation(value: str | None) -> str:
    t = (value or "").strip().lower()
    if t in {"can", "ca", "canadian"}:
        return "canada"
    if t in {"usa", "us", "united states", "united states of america", "american"}:
        return "usa"
    return t


def _is_mvp_award(name: str | None) -> bool:
    up = " ".join((name or "").upper().split())
    return any(n in up for n in _MVP_NEEDLES)


def _missed_playoffs(result: str | None) -> bool:
    t = (result or "").strip().lower()
    return (not t) or t.startswith("missed")


def _is_champion_result(result: str | None) -> bool:
    t = " ".join((result or "").upper().split())
    return t == CHAMPION_RESULT or t.endswith("CUP CHAMPION") or t == "CHAMPION"


def _rs_ranks(standings: list[TeamStanding]) -> dict[int, int]:
    ordered = sorted(
        standings,
        key=lambda s: (-int(s.pts or 0), -int(s.w or 0), int(s.gp or 0), int(s.team_id or 0)),
    )
    return {int(s.team_id): i + 1 for i, s in enumerate(ordered) if s.team_id is not None}


def _max_game_id(session: Session) -> int:
    return int(session.scalar(select(func.coalesce(func.max(Game.id), 0))) or 0)


def _active_memberships(site_session: Session, league_slug: str) -> dict[int, GmLeagueMembership]:
    rows = site_session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    return {int(r.team_id): r for r in rows if r.team_id is not None}


def _player_name(session: Session, player_id: int | None) -> str:
    if not player_id:
        return ""
    pl = session.get(Player, int(player_id))
    return (pl.full_name if pl else "") or ""


def _team_name(session: Session, team_id: int | None) -> str:
    if not team_id:
        return ""
    team = session.get(Team, int(team_id))
    return team.full_display_name() if team else ""


def _acquired_players_from_published_trades(session: Session, league_slug: str) -> dict[int, set[int]]:
    from app.services.trade_tool import STATUS_PUBLISHED, parse_ledger_payload

    acquired: dict[int, set[int]] = {}
    props = session.scalars(
        select(GmTradeProposal).where(
            GmTradeProposal.league_slug == league_slug,
            GmTradeProposal.status == STATUS_PUBLISHED,
        )
    ).all()
    for prop in props:
        left, right = parse_ledger_payload(prop.ledger_json)
        mapped = acquired_by_team_from_ledger(
            int(prop.from_team_id),
            int(prop.to_team_id),
            left,
            right,
        )
        for tid, pids in mapped.items():
            acquired.setdefault(int(tid), set()).update(pids)
    return acquired


def discover_true_achievements(
    session: Session,
    league_slug: str,
    *,
    tenure_counts: dict[int, int] | None = None,
    promoted_team_ids: set[int] | None = None,
    as_of_game_date: date | None = None,
) -> dict[int, dict[str, dict[str, Any]]]:
    """Return team_id -> {achievement_key: meta} for currently true achievements.

    ``as_of_game_date`` limits game-log feats to finals on or before that date so a
    heritage watermark can be rebuilt after a failed first evaluate.
    """
    allowed = {item.key for item in catalog_for_league(league_slug)}
    hits: dict[int, dict[str, dict[str, Any]]] = {}

    def mark(team_id: int | None, key: str, meta: dict[str, Any] | None = None) -> None:
        base = catalog_key_from_storage(key)
        if not team_id or (key not in allowed and base not in allowed):
            return
        bucket = hits.setdefault(int(team_id), {})
        if key not in bucket:
            bucket[key] = dict(meta or {})

    season = get_current_season()
    season_label = season_display_label(season)
    age_ref = date.today()
    if season and season.start_year:
        age_ref = date(int(season.start_year), 7, 1)

    standings = (
        list(session.scalars(select(TeamStanding).where(TeamStanding.season_id == season.id)).all())
        if season
        else []
    )
    rs_rank = _rs_ranks(standings)

    teams = list(session.scalars(select(Team)).all())
    team_by_id = {int(t.id): t for t in teams}

    records = list(session.scalars(select(TeamSeasonRecord)).all())
    records_by_team: dict[int, list[TeamSeasonRecord]] = {}
    for rec in records:
        if rec.team_id is None:
            continue
        records_by_team.setdefault(int(rec.team_id), []).append(rec)
    for recs in records_by_team.values():
        recs.sort(key=lambda r: (int(r.start_year or 0), r.season_year_label or ""))

    # --- game feats (current season when possible; all finals if no season) ---
    game_q = select(Game).where(Game.status == "final")
    if season:
        game_q = game_q.where(Game.season_id == season.id)
    if as_of_game_date is not None:
        game_q = game_q.where(Game.game_date <= as_of_game_date)
    games = list(session.scalars(game_q).all())
    game_ids = [int(g.id) for g in games]
    games_by_id = {int(g.id): g for g in games}

    skater_lines: list[GameSkaterStat] = []
    goalie_lines: list[GameGoalieStat] = []
    penalties: list[PenaltyEvent] = []
    scoring: list[ScoringEvent] = []
    if game_ids:
        skater_lines = list(
            session.scalars(select(GameSkaterStat).where(GameSkaterStat.game_id.in_(game_ids))).all()
        )
        goalie_lines = list(
            session.scalars(select(GameGoalieStat).where(GameGoalieStat.game_id.in_(game_ids))).all()
        )
        penalties = list(
            session.scalars(select(PenaltyEvent).where(PenaltyEvent.game_id.in_(game_ids))).all()
        )
        scoring = list(
            session.scalars(select(ScoringEvent).where(ScoringEvent.game_id.in_(game_ids))).all()
        )

    fights_by_game_player: set[tuple[int, int]] = set()
    fights_by_game_team: dict[tuple[int, int], int] = {}
    for pen in penalties:
        if not is_fighting_infraction(pen.infraction):
            continue
        if pen.player_id:
            fights_by_game_player.add((int(pen.game_id), int(pen.player_id)))
        if pen.team_id:
            key = (int(pen.game_id), int(pen.team_id))
            fights_by_game_team[key] = fights_by_game_team.get(key, 0) + 1

    scoring_by_game: dict[int, list[ScoringEvent]] = {}
    for ev in scoring:
        scoring_by_game.setdefault(int(ev.game_id), []).append(ev)

    player_team_by_game: dict[tuple[int, int], int] = {}
    skaters_by_game: dict[int, list[GameSkaterStat]] = {}
    for ln in skater_lines:
        if ln.player_id and ln.team_id:
            player_team_by_game[(int(ln.game_id), int(ln.player_id))] = int(ln.team_id)
        skaters_by_game.setdefault(int(ln.game_id), []).append(ln)
    for ln in goalie_lines:
        if ln.player_id and ln.team_id:
            player_team_by_game[(int(ln.game_id), int(ln.player_id))] = int(ln.team_id)

    players_by_id: dict[int, Player] = {}
    pids = {int(ln.player_id) for ln in skater_lines if ln.player_id}
    pids.update(int(ln.player_id) for ln in goalie_lines if ln.player_id)
    if pids:
        for pl in session.scalars(select(Player).where(Player.id.in_(pids))).all():
            players_by_id[int(pl.id)] = pl

    for ln in skater_lines:
        fought = (int(ln.game_id), int(ln.player_id)) in fights_by_game_player
        if detect_gordie_howe(goals=int(ln.goals or 0), assists=int(ln.assists or 0), fought=fought):
            mark(
                ln.team_id,
                "gordie_howe",
                {
                    "player_id": ln.player_id,
                    "player_name": _player_name(session, ln.player_id),
                    "game_id": ln.game_id,
                    "detail": f"{_player_name(session, ln.player_id)} recorded a Gordie Howe hat trick",
                },
            )
        if int(ln.goals or 0) >= FOUR_GOAL_MIN:
            mark(
                ln.team_id,
                "four_goal_night",
                {
                    "player_id": ln.player_id,
                    "player_name": _player_name(session, ln.player_id),
                    "game_id": ln.game_id,
                    "detail": f"{_player_name(session, ln.player_id)} scored {int(ln.goals)} goals",
                },
            )

    hat_trick_games: list[tuple[date, int, int, int, str]] = []
    for ln in skater_lines:
        if int(ln.goals or 0) < 3 or not ln.team_id:
            continue
        g = games_by_id.get(int(ln.game_id))
        if g is None:
            continue
        hat_trick_games.append(
            (
                g.game_date or date.max,
                int(g.id),
                int(ln.team_id),
                int(ln.player_id) if ln.player_id else 0,
                _player_name(session, ln.player_id),
            )
        )
    if hat_trick_games:
        hat_trick_games.sort(key=lambda row: (row[0], row[1], row[2]))
        first = hat_trick_games[0]
        mark(
            first[2],
            "league_first_hat",
            {
                "player_id": first[3] or None,
                "player_name": first[4],
                "game_id": first[1],
                "detail": f"{first[4] or 'A player'} scored the first hat trick of the season",
            },
        )

    four_goal_games: list[tuple[date, int, int, int, str]] = []
    for ln in skater_lines:
        if int(ln.goals or 0) < FOUR_GOAL_MIN or not ln.team_id:
            continue
        g = games_by_id.get(int(ln.game_id))
        if g is None:
            continue
        four_goal_games.append(
            (
                g.game_date or date.max,
                int(g.id),
                int(ln.team_id),
                int(ln.player_id) if ln.player_id else 0,
                _player_name(session, ln.player_id),
            )
        )
    if four_goal_games:
        four_goal_games.sort(key=lambda row: (row[0], row[1], row[2]))
        first = four_goal_games[0]
        mark(
            first[2],
            "league_first_four",
            {
                "player_id": first[3] or None,
                "player_name": first[4],
                "game_id": first[1],
                "detail": f"{first[4] or 'A player'} scored the first 4-goal game of the season",
            },
        )

    shutout_games: list[tuple[date, int, int, int, str]] = []
    for ln in goalie_lines:
        if int(ln.goals_allowed or 0) != 0 or int(ln.shots_against or 0) <= 0 or not ln.team_id:
            continue
        g = games_by_id.get(int(ln.game_id))
        if g is None:
            continue
        shutout_games.append(
            (
                g.game_date or date.max,
                int(g.id),
                int(ln.team_id),
                int(ln.player_id) if ln.player_id else 0,
                _player_name(session, ln.player_id),
            )
        )
    if shutout_games:
        shutout_games.sort(key=lambda row: (row[0], row[1], row[2]))
        first = shutout_games[0]
        mark(
            first[2],
            "league_first_shutout",
            {
                "player_id": first[3] or None,
                "player_name": first[4],
                "game_id": first[1],
                "detail": f"{first[4] or 'A goalie'} recorded the first shutout of the season",
            },
        )

    for gid, events in scoring_by_game.items():
        pid = detect_natural_hat_trick(events)
        if not pid:
            continue
        g = games_by_id.get(gid)
        team_id = None
        for ev in events:
            if ev.scorer_player_id == pid and ev.scoring_team_id:
                team_id = ev.scoring_team_id
                break
        mark(
            team_id,
            "all_natural",
            {
                "player_id": pid,
                "player_name": _player_name(session, pid),
                "game_id": gid,
                "detail": f"{_player_name(session, pid)} scored a natural hat trick",
            },
        )

    for g in games:
        for tid, is_home in ((g.home_team_id, True), (g.away_team_id, False)):
            if detect_goalie_win_1_0_40(
                home_score=g.home_score,
                away_score=g.away_score,
                home_shots=g.home_shots,
                away_shots=g.away_shots,
                team_is_home=is_home,
            ):
                mark(
                    tid,
                    "goalie_win",
                    {"game_id": g.id, "detail": "Won 1–0 while allowing 40+ shots"},
                )

        team_lines: dict[int, list[GameSkaterStat]] = {}
        for ln in skater_lines:
            if ln.game_id == g.id:
                team_lines.setdefault(int(ln.team_id), []).append(ln)
        for tid, lines in team_lines.items():
            team = team_by_id.get(tid)
            want = _nation_for_team(team)
            if not want or not lines:
                continue
            nations = []
            ok = True
            for ln in lines:
                pl = players_by_id.get(int(ln.player_id))
                nat = _norm_nation(pl.nationality if pl else None)
                if not nat:
                    ok = False
                    break
                nations.append(nat)
            if ok and nations and all(n == want for n in nations):
                mark(tid, "nationalism", {"game_id": g.id, "detail": "All-national lineup"})

    playoff_ids = {
        int(pg.home_team_id)
        for pg in games
        if is_playoff_game_type(pg.game_type) and pg.home_team_id
    } | {
        int(pg.away_team_id)
        for pg in games
        if is_playoff_game_type(pg.game_type) and pg.away_team_id
    }
    playoff_cutoff = len(playoff_ids) if playoff_ids else playoff_spot_cutoff(len(rs_rank))

    for g in games:
        winner_id, loser_id = _game_winner_loser(g)
        events = scoring_by_game.get(int(g.id), [])
        if winner_id and (
            detect_comeback_from_events(
                events,
                home_team_id=g.home_team_id,
                away_team_id=g.away_team_id,
                winner_id=winner_id,
            )
            or (
                not events
                and detect_comeback_from_period_scores(g, winner_id=winner_id)
            )
        ):
            mark(
                winner_id,
                "comeback_kids",
                {"game_id": g.id, "detail": f"Won after trailing by {COMEBACK_DEFICIT}+ goals"},
            )

        on_date = g.game_date or age_ref
        kids_by_team: dict[int, set[int]] = {}
        for ln in skaters_by_game.get(int(g.id), []):
            if int(ln.goals or 0) < 1 or not ln.team_id or not ln.player_id:
                continue
            pl = players_by_id.get(int(ln.player_id))
            age = player_age_on(pl.birth_date if pl else None, on_date)
            if age is not None and age <= KID_LINE_MAX_AGE:
                kids_by_team.setdefault(int(ln.team_id), set()).add(int(ln.player_id))
        for tid, kids in kids_by_team.items():
            if len(kids) >= KID_LINE_SCORERS:
                mark(
                    tid,
                    "kid_line_energy",
                    {
                        "game_id": g.id,
                        "count": len(kids),
                        "detail": f"{len(kids)} players 22 or younger scored in the same game",
                    },
                )

        for tid in (g.home_team_id, g.away_team_id):
            if tid and fights_by_game_team.get((int(g.id), int(tid)), 0) >= FIGHT_NIGHT_MIN:
                mark(
                    tid,
                    "fight_night",
                    {
                        "game_id": g.id,
                        "count": fights_by_game_team[(int(g.id), int(tid))],
                        "detail": f"{fights_by_game_team[(int(g.id), int(tid))]} fighting majors in one game",
                    },
                )

        if winner_id and is_playoff_game_type(g.game_type):
            ot_pid, ot_tid = detect_playoff_ot_winner(events, g)
            if ot_tid:
                pname = _player_name(session, ot_pid)
                mark(
                    ot_tid,
                    "playoff_ot_hero",
                    {
                        "player_id": ot_pid,
                        "player_name": pname,
                        "game_id": g.id,
                        "detail": f"{pname or 'A player'} scored a playoff OT winner",
                    },
                )

        if winner_id and loser_id and rs_rank:
            w_rank = rs_rank.get(int(winner_id))
            l_rank = rs_rank.get(int(loser_id))
            outside = (
                int(winner_id) not in playoff_ids
                if playoff_ids
                else (w_rank is not None and w_rank > playoff_cutoff)
            )
            if l_rank == 1 and outside:
                mark(
                    winner_id,
                    "statement_win",
                    {
                        "game_id": g.id,
                        "detail": f"Beat {_team_name(session, loser_id)} from outside a playoff spot",
                    },
                )

    # --- season counting stats ---
    if season:
        use_season_stat_lines = as_of_game_date is None
        skaters = (
            list(
                session.scalars(
                    select(PlayerSkaterStat).where(
                        PlayerSkaterStat.season_id == season.id,
                        PlayerSkaterStat.stat_segment == "rs",
                    )
                ).all()
            )
            if use_season_stat_lines
            else []
        )
        goalies = (
            list(
                session.scalars(
                    select(PlayerGoalieStat).where(
                        PlayerGoalieStat.season_id == season.id,
                        PlayerGoalieStat.stat_segment == "rs",
                    )
                ).all()
            )
            if use_season_stat_lines
            else []
        )
        for st in skaters:
            if int(st.points or 0) >= 100:
                mark(
                    st.team_id,
                    "century_club",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} recorded {int(st.points)} points",
                    },
                )
            if int(st.points or 0) >= 200:
                mark(
                    st.team_id,
                    "two_hundred",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} recorded {int(st.points)} points",
                    },
                )
            if int(st.goals or 0) >= 50:
                mark(
                    st.team_id,
                    "fifty_goals",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} scored {int(st.goals)} goals",
                    },
                )
            if int(st.goals or 0) >= 50 and int(st.gp or 0) <= 50:
                mark(
                    st.team_id,
                    "rocket",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} scored {int(st.goals)} in {int(st.gp)} GP",
                    },
                )
        if any(int(st.goals or 0) >= 50 and int(st.gp or 0) > 50 for st in skaters):
            _mark_rocket_from_game_logs(session, season.id, skaters, games, skater_lines, mark)

        for st in goalies:
            if int(st.wins or 0) >= 40:
                mark(
                    st.team_id,
                    "forty_win_goalie",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} won {int(st.wins)} games",
                    },
                )

        roster = list(
            session.scalars(select(Player).where(Player.current_team_id.is_not(None))).all()
        )
        by_team_roster: dict[int, list[Player]] = {}
        for pl in roster:
            if pl.retired:
                continue
            by_team_roster.setdefault(int(pl.current_team_id), []).append(pl)
        for tid, plist in by_team_roster.items():
            seniors = 0
            hometown = 0
            team = team_by_id.get(tid)
            for pl in plist:
                if pl.birth_date:
                    years = age_ref.year - pl.birth_date.year
                    if (age_ref.month, age_ref.day) < (pl.birth_date.month, pl.birth_date.day):
                        years -= 1
                    if years >= 40:
                        seniors += 1
                if team and _cities_match(pl.birth_city, team.city):
                    hometown += 1
            if seniors >= 3:
                mark(tid, "senior_team", {"count": seniors, "detail": f"{seniors} players age 40+"})
            if hometown >= 3:
                mark(tid, "hometown_roster", {"count": hometown, "detail": f"{hometown} hometown players"})

        drafted_by_team: dict[int, set[int]] = {}
        for pick in session.scalars(select(DraftPick).where(DraftPick.player_id.is_not(None))).all():
            if pick.team_id and pick.player_id:
                drafted_by_team.setdefault(int(pick.team_id), set()).add(int(pick.player_id))
        for tid, plist in by_team_roster.items():
            grown = [pl for pl in plist if int(pl.id) in drafted_by_team.get(tid, set())]
            if len(grown) >= HOMEGROWN_CORE_TARGET:
                mark(
                    tid,
                    "homegrown_core",
                    {"count": len(grown), "detail": f"{len(grown)} self-drafted players on the roster"},
                )

        if rs_rank:
            first_id = next((tid for tid, r in rs_rank.items() if r == 1), None)
            playoffs_started = any(is_playoff_game_type(g.game_type) for g in games)
            if first_id and playoffs_started:
                mark(first_id, "presidents", {"detail": "Finished first in the regular season"})

        road_wins: dict[int, int] = {}
        for g in games:
            if is_playoff_game_type(g.game_type):
                continue
            if g.home_score is None or g.away_score is None:
                continue
            if int(g.away_score) > int(g.home_score):
                road_wins[int(g.away_team_id)] = road_wins.get(int(g.away_team_id), 0) + 1
        if road_wins and any(is_playoff_game_type(g.game_type) for g in games):
            best = max(road_wins.values())
            leaders = [tid for tid, n in road_wins.items() if n == best]
            if len(leaders) == 1:
                mark(leaders[0], "road_warrior", {"wins": best, "detail": f"Led the league with {best} road wins"})

        year_recs = [
            r
            for r in records
            if season_label and r.season_year_label == season_label and r.pp_pct is not None and r.pk_pct is not None
        ]
        if year_recs and as_of_game_date is None:
            best_pp = max(float(r.pp_pct or 0) for r in year_recs)
            best_pk = max(float(r.pk_pct or 0) for r in year_recs)
            for r in year_recs:
                if r.team_id and float(r.pp_pct or 0) == best_pp and float(r.pk_pct or 0) == best_pk:
                    mark(r.team_id, "youre_special", {"detail": "Led the league in PP% and PK%"})

        home_wins: dict[int, int] = {}
        ot_wins: dict[int, int] = {}
        wins_vs: dict[int, dict[int, int]] = {}
        results_by_team: dict[int, list[str]] = {}
        first_stars: dict[int, dict[int, int]] = {}
        chrono_games = sorted(games, key=lambda g: (g.game_date or date.min, int(g.id)))
        for g in chrono_games:
            winner_id, loser_id = _game_winner_loser(g)
            playoff = is_playoff_game_type(g.game_type)
            if winner_id and loser_id:
                if not playoff and int(winner_id) == int(g.home_team_id):
                    home_wins[int(winner_id)] = home_wins.get(int(winner_id), 0) + 1
                if not playoff and (g.went_to_overtime or g.went_to_shootout):
                    ot_wins[int(winner_id)] = ot_wins.get(int(winner_id), 0) + 1
                opp = int(loser_id)
                bucket = wins_vs.setdefault(int(winner_id), {})
                bucket[opp] = bucket.get(opp, 0) + 1
            for tid in (g.home_team_id, g.away_team_id):
                if not tid or not is_regular_season_game_type(g.game_type):
                    continue
                letter = game_outcome_letter(g, int(tid))
                if letter:
                    results_by_team.setdefault(int(tid), []).append(letter)
            star_pid = getattr(g, "fhm_star1_player_id", None)
            if star_pid:
                star_tid = player_team_by_game.get((int(g.id), int(star_pid)))
                if star_tid:
                    team_stars = first_stars.setdefault(int(star_tid), {})
                    team_stars[int(star_pid)] = team_stars.get(int(star_pid), 0) + 1

        for tid, letters in results_by_team.items():
            streak = max_win_streak(letters)
            if streak >= WIN_STREAK_TARGET:
                mark(tid, "on_a_heater", {"streak": streak, "detail": f"{streak}-game win streak"})

        playoffs_started = any(is_playoff_game_type(g.game_type) for g in games)
        if home_wins and playoffs_started:
            best_home = max(home_wins.values())
            home_leaders = [tid for tid, n in home_wins.items() if n == best_home]
            if len(home_leaders) == 1:
                mark(
                    home_leaders[0],
                    "home_cooking",
                    {"wins": best_home, "detail": f"Led the league with {best_home} home wins"},
                )

        for tid, n in ot_wins.items():
            if n >= OT_WINS_TARGET:
                mark(tid, "overtime_merchant", {"wins": n, "detail": f"{n} overtime or shootout wins"})

        for tid, by_player in first_stars.items():
            best_pid, best_n = max(by_player.items(), key=lambda item: item[1])
            if best_n >= FIRST_STAR_TARGET:
                pname = _player_name(session, best_pid)
                mark(
                    tid,
                    "three_star_season",
                    {
                        "player_id": best_pid,
                        "player_name": pname,
                        "count": best_n,
                        "detail": f"{pname} was first star {best_n} times",
                    },
                )

        for tid, by_opp in wins_vs.items():
            if not by_opp:
                continue
            opp_id, n = max(by_opp.items(), key=lambda item: item[1])
            if n >= NEMESIS_WINS_TARGET:
                mark(
                    tid,
                    "nemesis",
                    {
                        "opponent_team_id": opp_id,
                        "wins": n,
                        "detail": f"Beat {_team_name(session, opp_id)} {n} times",
                    },
                )

        contracts = {
            int(c.player_id): c
            for c in session.scalars(select(PlayerContract)).all()
            if c.player_id
        }
        for st in skaters:
            if int(st.goals or 0) < 30 or not st.player_id or not st.team_id:
                continue
            contract = contracts.get(int(st.player_id))
            salary = int(contract.average_salary) if contract and contract.average_salary is not None else None
            if salary is None or salary <= 0 or salary >= BARGAIN_BIN_SALARY_CAP:
                continue
            pname = _player_name(session, st.player_id)
            mark(
                st.team_id,
                "bargain_bin",
                {
                    "player_id": st.player_id,
                    "player_name": pname,
                    "detail": f"{pname} scored {int(st.goals)} goals on a sub-$1M deal",
                },
            )

        for st in skaters:
            if int(st.goals or 0) < ELC_GOALS_TARGET or not st.player_id or not st.team_id:
                continue
            contract = contracts.get(int(st.player_id))
            if not contract or not contract.is_elc:
                continue
            pname = _player_name(session, st.player_id)
            mark(
                st.team_id,
                "elc_lightning",
                {
                    "player_id": st.player_id,
                    "player_name": pname,
                    "detail": f"{pname} scored {int(st.goals)} goals on an ELC",
                },
            )

        if year_recs and as_of_game_date is None:
            pp_ranked = sorted(year_recs, key=lambda r: float(r.pp_pct or 0), reverse=True)
            pk_ranked = sorted(year_recs, key=lambda r: float(r.pk_pct or 0), reverse=True)
            top_pp = {int(r.team_id) for r in pp_ranked[:3] if r.team_id}
            top_pk = {int(r.team_id) for r in pk_ranked[:3] if r.team_id}
            for tid in top_pp & top_pk:
                mark(tid, "special_teams_season", {"detail": "Top 3 in PP% and PK%"})

        by_month: dict[tuple[int, str], list[str]] = {}
        for g in chrono_games:
            if not is_regular_season_game_type(g.game_type) or not g.game_date:
                continue
            period = g.game_date.strftime("%Y-%m")
            for tid in (g.home_team_id, g.away_team_id):
                if not tid:
                    continue
                letter = game_outcome_letter(g, int(tid))
                if letter:
                    by_month.setdefault((int(tid), period), []).append(letter)
        month_schedule = (
            list(session.scalars(select(Game).where(Game.season_id == season.id)).all())
            if season
            else list(session.scalars(select(Game)).all())
        )
        for (tid, period), letters in by_month.items():
            if not regular_season_month_is_complete(
                month_schedule, period, as_of_game_date=as_of_game_date
            ):
                continue
            if month_undefeated(letters):
                mark(
                    tid,
                    f"the_bender:{period}",
                    {
                        "period": period,
                        "games": len(letters),
                        "detail": f"Undefeated in {period} ({len(letters)} GP)",
                    },
                )

        try:
            acquired = _acquired_players_from_published_trades(session, league_slug)
        except Exception:
            _log.exception("GM achievements: Trade Tool lookup failed for The Heist.")
            acquired = {}
        if acquired:
            productions: list[tuple[int, int, int]] = []
            for st in skaters:
                if st.team_id and st.player_id:
                    productions.append((int(st.team_id), int(st.player_id), int(st.points or 0)))
            year = int(season.start_year or 0) if season else 0
            pids = {pid for ids in acquired.values() for pid in ids}
            if year and pids:
                for line in session.scalars(
                    select(PlayerSkaterCareerLine).where(
                        PlayerSkaterCareerLine.player_id.in_(pids),
                        PlayerSkaterCareerLine.season_year == year,
                        PlayerSkaterCareerLine.career_source == "rs",
                    )
                ).all():
                    if line.team_id and line.player_id:
                        pts = int(line.goals or 0) + int(line.assists or 0)
                        productions.append((int(line.team_id), int(line.player_id), pts))
            for tid, pid, pts in detect_heist(acquired, productions):
                pname = _player_name(session, pid)
                mark(
                    tid,
                    "the_heist",
                    {
                        "player_id": pid,
                        "player_name": pname,
                        "points": pts,
                        "detail": f"{pname} recorded {pts} points after the trade",
                    },
                )

    # --- playoffs / cups ---
    po_games = [g for g in games if is_playoff_game_type(g.game_type)]
    for g in po_games:
        mark(g.home_team_id, "make_playoffs", {"detail": "Made the playoffs"})
        mark(g.away_team_id, "make_playoffs", {"detail": "Made the playoffs"})

    for recs in records_by_team.values():
        for rec in recs:
            if rec.team_id and not _missed_playoffs(rec.result) and rec.result:
                mark(rec.team_id, "make_playoffs", {"season": rec.season_year_label, "detail": rec.result})

    series_list = build_playoff_series(po_games, rs_rank)
    playoff_ranks = {tid: rs_rank[tid] for tid in {s.team_a for s in series_list} | {s.team_b for s in series_list} if tid in rs_rank}
    champ_team_ids: set[int] = set()
    for recs in records_by_team.values():
        for rec in recs:
            if rec.team_id and _is_champion_result(rec.result):
                champ_team_ids.add(int(rec.team_id))
                mark(rec.team_id, "pinnacle", {"season": rec.season_year_label, "detail": "Won the BOWL Cup"})
    for row in session.scalars(select(HistoryChampion)).all():
        if row.team_id:
            champ_team_ids.add(int(row.team_id))
            mark(row.team_id, "pinnacle", {"detail": "Won the BOWL Cup"})

    for ser in series_list:
        if ser.winner_id and ser.is_upset():
            mark(ser.winner_id, "upset", {"detail": "Won a playoff series as the lower seed"})
        if ser.winner_id and ser.trailed_3_2_then_won():
            mark(ser.winner_id, "guarantee", {"detail": "Won a 7-game series after trailing 3–2"})
        if ser.winner_id and ser.is_sweep:
            mark(ser.winner_id, "playoff_sweep", {"detail": "Swept a playoff series"})
            loser_id = ser.team_b if ser.winner_id == ser.team_a else ser.team_a
            mark(loser_id, "swept_not_forgotten", {"detail": "Got swept in a playoff series"})
        if ser.winner_id and detect_road_win_after_dropping_first_two(ser.games, ser.team_a):
            mark(
                ser.team_a,
                "guarantee_remixed",
                {"detail": "Won a playoff game on the road after dropping the first two"},
            )
        if ser.winner_id and detect_road_win_after_dropping_first_two(ser.games, ser.team_b):
            mark(
                ser.team_b,
                "guarantee_remixed",
                {"detail": "Won a playoff game on the road after dropping the first two"},
            )
        if ser.winner_id and ser.trailed_0_3_then_won():
            mark(ser.winner_id, "reverse_sweep", {"detail": "Won a series after trailing 0–3"})
        if ser.winner_id and ser.winner_id in champ_team_ids and ser.winner_is_lowest_seed(playoff_ranks):
            mark(ser.winner_id, "cup_eight_seed", {"detail": "Won the Cup as the lowest playoff seed"})

    po_order = {int(g.id): i for i, g in enumerate(sorted(po_games, key=lambda g: (g.game_date or date.min, int(g.id))))}
    po_goalie_by_team: dict[int, list[tuple[int, int, int]]] = {}
    for ln in goalie_lines:
        if ln.game_id not in po_order or not ln.team_id or not ln.player_id:
            continue
        if int(ln.shots_against or 0) <= 0 and int(ln.toi_seconds or 0) <= 0:
            continue
        po_goalie_by_team.setdefault(int(ln.team_id), []).append(
            (po_order[int(ln.game_id)], int(ln.player_id), int(ln.goals_allowed or 0))
        )
    for tid, rows in po_goalie_by_team.items():
        rows.sort(key=lambda r: (r[0], r[1]))
        if detect_consecutive_playoff_shutouts(rows):
            mark(tid, "playoff_shutout_pair", {"detail": "Consecutive playoff shutouts"})

    drafted_ids_by_team: dict[int, set[int]] = {}
    for pick in session.scalars(select(DraftPick).where(DraftPick.player_id.is_not(None), DraftPick.team_id.is_not(None))).all():
        drafted_ids_by_team.setdefault(int(pick.team_id), set()).add(int(pick.player_id))
    po_roster: dict[int, set[int]] = {}
    for ln in skater_lines + goalie_lines:
        if ln.game_id in po_order and ln.team_id and ln.player_id:
            po_roster.setdefault(int(ln.team_id), set()).add(int(ln.player_id))
    for tid in champ_team_ids:
        grown = po_roster.get(tid, set()) & drafted_ids_by_team.get(tid, set())
        if len(grown) >= HOMEGROWN_CUP_TARGET:
            mark(
                tid,
                "homegrown_cup",
                {"count": len(grown), "detail": f"Won the Cup with {len(grown)} self-drafted playoff players"},
            )

    for tid, recs in records_by_team.items():
        champ_years = [int(r.start_year) for r in recs if r.start_year and _is_champion_result(r.result)]
        champ_years.sort()
        streak = 0
        best = 0
        prev = None
        for y in champ_years:
            if prev is not None and y == prev + 1:
                streak += 1
            else:
                streak = 1
            best = max(best, streak)
            prev = y
        if best >= 5:
            mark(tid, "dynasty", {"streak": best, "detail": f"{best} consecutive championships"})

        playoff_years = sorted(
            {
                int(r.start_year)
                for r in recs
                if r.start_year and r.result and not _missed_playoffs(r.result)
            }
        )
        po_streak = po_best = 0
        prev_y = None
        for y in playoff_years:
            po_streak = po_streak + 1 if prev_y is not None and y == prev_y + 1 else 1
            po_best = max(po_best, po_streak)
            prev_y = y
        if po_best >= IRON_DECADE_TARGET:
            mark(tid, "iron_decade", {"streak": po_best, "detail": f"{po_best} consecutive playoff appearances"})

        # Zero to hero: last overall previous year, champion this year
        by_year = {int(r.start_year): r for r in recs if r.start_year is not None}
        for year, rec in by_year.items():
            if not _is_champion_result(rec.result):
                continue
            prev_recs = [
                r
                for r in records
                if r.start_year == year - 1 and r.pts is not None
            ]
            if not prev_recs:
                continue
            worst_pts = min(int(r.pts or 0) for r in prev_recs)
            prev_self = by_year.get(year - 1)
            if prev_self and prev_self.pts is not None and int(prev_self.pts) == worst_pts:
                mark(tid, "zero_to_hero", {"season": rec.season_year_label, "detail": "Last to champion"})

    # --- draft 54 ---
    for pick in session.scalars(select(DraftPick).where(DraftPick.overall_pick == 54)).all():
        if pick.team_id:
            mark(
                pick.team_id,
                "game54",
                {
                    "player_id": pick.player_id,
                    "player_name": _player_name(session, pick.player_id),
                    "detail": f"Drafted {_player_name(session, pick.player_id) or 'a player'} 54th overall",
                },
            )

    late_picks = list(
        session.scalars(
            select(DraftPick).where(
                DraftPick.overall_pick >= DRAFT_STEAL_OVERALL,
                DraftPick.player_id.is_not(None),
                DraftPick.team_id.is_not(None),
            )
        ).all()
    )
    if late_picks:
        late_pids = {int(p.player_id) for p in late_picks if p.player_id}
        steal_stat_rows = list(
            session.scalars(
                select(PlayerSkaterStat).where(
                    PlayerSkaterStat.player_id.in_(late_pids),
                    PlayerSkaterStat.stat_segment == "rs",
                    PlayerSkaterStat.points >= DRAFT_STEAL_POINTS,
                )
            ).all()
        )
        steal_points = {int(st.player_id) for st in steal_stat_rows if st.player_id}
        steal_stars = {
            int(row.player_id)
            for row in session.scalars(
                select(HistoryAllStar).where(HistoryAllStar.player_id.in_(late_pids))
            ).all()
            if row.player_id
        }
        steal_pids = steal_points | steal_stars
        for pick in late_picks:
            if not pick.player_id or int(pick.player_id) not in steal_pids:
                continue
            pname = _player_name(session, pick.player_id)
            mark(
                pick.team_id,
                "draft_steal",
                {
                    "player_id": pick.player_id,
                    "player_name": pname,
                    "overall_pick": pick.overall_pick,
                    "detail": f"{pname or 'A player'} (pick {int(pick.overall_pick)}) became a late-round steal",
                },
            )

    # --- MVP first season on club ---
    awards = list(session.scalars(select(HistoryAward)).all())
    for aw in awards:
        if not _is_mvp_award(aw.award_name) or not aw.player_id or not aw.team_id:
            continue
        lines = list(
            session.scalars(
                select(PlayerSkaterCareerLine).where(PlayerSkaterCareerLine.player_id == aw.player_id)
            ).all()
        )
        if not lines:
            mark(aw.team_id, "new_team_mvp", {"player_id": aw.player_id, "detail": "MVP in first season on the club"})
            continue
        team = team_by_id.get(int(aw.team_id))
        fhm = None
        if team and team.fhm_team_id is not None:
            try:
                fhm = int(team.fhm_team_id)
            except (TypeError, ValueError):
                fhm = None
        years = sorted({int(ln.season_year) for ln in lines if ln.season_year is not None})
        on_club = [
            int(ln.season_year)
            for ln in lines
            if ln.season_year is not None and fhm is not None and int(ln.team_fhm_id or -1) == fhm
        ]
        if on_club and min(on_club) == (years[0] if years else min(on_club)):
            mark(
                aw.team_id,
                "new_team_mvp",
                {
                    "player_id": aw.player_id,
                    "player_name": _player_name(session, aw.player_id),
                    "detail": f"{_player_name(session, aw.player_id)} won MVP in a first season on the club",
                },
            )
        elif on_club and min(on_club) == min(on_club):
            # first year on this club (may have prior years elsewhere)
            first_here = min(on_club)
            award_year = None
            notes = aw.notes or ""
            m = re.search(r"(\d{4}-\d{2})", notes)
            if m:
                try:
                    award_year = int(m.group(1).split("-")[0])
                except ValueError:
                    award_year = None
            if award_year is None or award_year == first_here:
                mark(
                    aw.team_id,
                    "new_team_mvp",
                    {
                        "player_id": aw.player_id,
                        "player_name": _player_name(session, aw.player_id),
                        "detail": f"{_player_name(session, aw.player_id)} won MVP in a first season on the club",
                    },
                )

    for aw in awards:
        if not is_calder_award(aw.award_name) or not aw.team_id:
            continue
        pname = _player_name(session, aw.player_id)
        mark(
            aw.team_id,
            "calder_club",
            {
                "player_id": aw.player_id,
                "player_name": pname,
                "detail": f"{pname or 'A player'} won the Calder Trophy",
            },
        )

    shelf: dict[tuple[int, int], set[str]] = {}
    for aw in awards:
        slot = major_award_slot(aw.award_name)
        if not slot or not aw.team_id or not aw.season_id:
            continue
        shelf.setdefault((int(aw.team_id), int(aw.season_id)), set()).add(slot)
    for (tid, _sid), slots in shelf.items():
        if len(slots) >= AWARD_SHELF_TARGET:
            mark(
                tid,
                "award_shelf",
                {"count": len(slots), "detail": f"Won {len(slots)} different major awards in one season"},
            )

    if tenure_counts:
        for tid, n in tenure_counts.items():
            if n >= 10:
                mark(tid, "true_franchise", {"seasons": n, "detail": f"Managed the franchise for {n} seasons"})

    if promoted_team_ids:
        for tid in promoted_team_ids:
            mark(tid, "going_up", {"detail": "Promoted to the higher league"})

    attendance_rows = list(
        session.scalars(select(GmExportAttendance).where(GmExportAttendance.league_slug == league_slug)).all()
    )
    dates_by_team: dict[int, list[date]] = {}
    for row in attendance_rows:
        if row.team_id and row.export_date:
            dates_by_team.setdefault(int(row.team_id), []).append(row.export_date)
    for tid, dates in dates_by_team.items():
        streak = export_streak_len(dates)
        if streak >= EXPORT_STREAK_TARGET:
            mark(
                tid,
                "export_streak",
                {"streak": streak, "detail": f"{streak} consecutive scheduled exports"},
            )

    try:
        from app.services.export_attendance import ATTENDANCE_WINDOW_DAYS, rolling_attendance_window_dates
    except Exception:
        ATTENDANCE_WINDOW_DAYS = 45
        rolling_attendance_window_dates = None
    if rolling_attendance_window_dates is not None:
        window = set(rolling_attendance_window_dates())
        league_days = {
            row.export_date
            for row in attendance_rows
            if row.export_date and row.export_date in window
        }
        if len(league_days) >= 2:
            for tid, dates in dates_by_team.items():
                team_days = {d for d in dates if d in window}
                if league_days <= team_days:
                    mark(
                        tid,
                        "perfect_attendance",
                        {
                            "count": len(league_days),
                            "detail": f"Hit all {len(league_days)} league exports in {ATTENDANCE_WINDOW_DAYS} days",
                        },
                    )

    return hits


def _mark_rocket_from_game_logs(
    session: Session,
    season_id: int,
    skaters: list[PlayerSkaterStat],
    games: list[Game],
    skater_lines: list[GameSkaterStat],
    mark,
) -> None:
    rs_games = [g for g in games if not is_playoff_game_type(g.game_type)]
    rs_games.sort(key=lambda g: (g.game_date or date.min, int(g.id)))
    order = {int(g.id): i for i, g in enumerate(rs_games)}
    goals_by_player: dict[int, list[tuple[int, int, int]]] = {}
    for ln in skater_lines:
        g = next((x for x in rs_games if x.id == ln.game_id), None)
        if g is None or not ln.player_id:
            continue
        goals_by_player.setdefault(int(ln.player_id), []).append(
            (order.get(int(g.id), 9999), int(ln.goals or 0), int(ln.team_id))
        )
    candidates = [st for st in skaters if int(st.goals or 0) >= 50 and int(st.gp or 0) > 50]
    for st in candidates:
        rows = sorted(goals_by_player.get(int(st.player_id), []), key=lambda r: r[0])
        total = 0
        gp = 0
        for _idx, goals, tid in rows:
            gp += 1
            total += goals
            if total >= 50 and gp <= 50:
                mark(
                    tid,
                    "rocket",
                    {
                        "player_id": st.player_id,
                        "player_name": _player_name(session, st.player_id),
                        "detail": f"{_player_name(session, st.player_id)} scored 50 goals in {gp} GP",
                    },
                )
                break


def collect_new_hits(
    truths: dict[int, dict[str, dict[str, Any]]],
    already: set[tuple[int, str]],
    existing: set[tuple[int, str]],
) -> list[tuple[int, str, dict[str, Any]]]:
    """Return newly true (team_id, key, meta) pairs that are not seeded or unlocked."""
    hits: list[tuple[int, str, dict[str, Any]]] = []
    for team_id, keys in truths.items():
        for key, meta in keys.items():
            pair = (int(team_id), str(key))
            if pair in already or pair in existing:
                continue
            hits.append((int(team_id), str(key), dict(meta or {})))
    return hits


def _already_pairs(watermark: GmAchievementWatermark | None) -> set[tuple[int, str]]:
    out: set[tuple[int, str]] = set()
    if watermark is None:
        return out
    for tid, keys in watermark.already_true_map().items():
        try:
            team_id = int(tid)
        except ValueError:
            continue
        for key in keys:
            out.add((team_id, key))
    return out


def _pairs_to_json(pairs: set[tuple[int, str]]) -> str:
    by_team: dict[str, list[str]] = {}
    for tid, key in sorted(pairs):
        by_team.setdefault(str(tid), []).append(key)
    for keys in by_team.values():
        keys.sort()
    return json.dumps(by_team)


def _team_tiers_now(session: Session, league_slug: str) -> dict[str, str]:
    if league_slug != "bowl-fantasy":
        return {}
    try:
        from app.services.relegation import get_tier_config, team_tier
    except Exception:
        return {}
    try:
        config = get_tier_config(session)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for team in session.scalars(select(Team)).all():
        tier = team_tier(team, config)
        if tier:
            out[str(int(team.id))] = str(tier)
    return out


def _promoted_ids(prev: dict[str, str], current: dict[str, str]) -> set[int]:
    out: set[int] = set()
    for tid, now in current.items():
        if prev.get(tid) == "lower" and now == "upper":
            try:
                out.add(int(tid))
            except ValueError:
                continue
    return out


def _achievement_ap_meta(spec: AchievementDef, storage_key: str) -> dict[str, Any]:
    return {
        "achievement_key": storage_key,
        "achievement_title": spec.title,
        "note": f"Achievement: {spec.title}",
    }


def credit_achievement_ap(
    *,
    league_slug: str,
    team_id: int,
    spec: AchievementDef,
    source_ref: str,
    created_by_user_id: int | None,
    storage_key: str | None = None,
    delta: int | None = None,
) -> Any:
    """Write the AP ledger row for an unlock. Idempotent on source_ref."""
    from app.services.ap_service import add_ledger_entry

    amount = int(delta if delta is not None else spec.ap)
    if amount <= 0:
        return None
    return add_ledger_entry(
        league_slug=league_slug,
        team_id=int(team_id),
        delta=amount,
        reason_code=REASON_CODE,
        meta=_achievement_ap_meta(spec, storage_key or spec.key),
        created_by_user_id=created_by_user_id,
        source_ref=source_ref,
    )


def sync_achievement_ap_ledger(session: Session, league_slug: str) -> int:
    """Create missing ledger rows for unlocks that already claimed AP."""
    from app.site_models import ApLedgerEntry

    mark_heritage_claimed_unlocks(session, league_slug)
    created = 0
    unlocks = list(
        session.scalars(
            select(GmAchievementUnlock).where(GmAchievementUnlock.league_slug == league_slug)
        ).all()
    )
    if not unlocks:
        return 0
    refs = [str(row.source_ref) for row in unlocks if row.source_ref]
    existing_refs: set[str] = set()
    if refs:
        existing_refs = {
            str(ref)
            for ref in session.scalars(
                select(ApLedgerEntry.source_ref).where(ApLedgerEntry.source_ref.in_(refs))
            ).all()
            if ref
        }
    for unlock in unlocks:
        if unlock.claimed_at is None:
            continue
        if int(unlock.ap_delta or 0) <= 0 or not unlock.source_ref:
            continue
        if str(unlock.source_ref) in existing_refs:
            continue
        spec = CATALOG_BY_KEY.get(catalog_key_from_storage(unlock.achievement_key))
        if spec is None:
            continue
        row = credit_achievement_ap(
            league_slug=str(unlock.league_slug),
            team_id=int(unlock.team_id),
            spec=spec,
            source_ref=str(unlock.source_ref),
            created_by_user_id=int(unlock.user_id) if unlock.user_id else None,
            storage_key=str(unlock.achievement_key),
            delta=int(unlock.ap_delta),
        )
        if row is not None:
            existing_refs.add(str(unlock.source_ref))
            created += 1
    return created


def reseed_gm_achievement_watermark(app, *, as_of_game_date: date) -> dict[str, int]:
    """Replace the heritage snapshot with truths as of ``as_of_game_date``, then award later feats."""
    slug = str(getattr(app, "config", {}).get("LEAGUE_SLUG") or "").strip()
    stats = {"reseeds": 0, "seeded": 0, "awarded": 0, "skipped": 0, "ledger_synced": 0}
    if slug not in HOCKEY_SLUGS:
        stats["skipped"] = 1
        return stats

    from app.league_db import db
    from app.sqlite_retry import commit_with_sqlite_retry

    session = db.session
    season = get_current_season()
    season_label = season_display_label(season)
    truths = rewrite_truths_to_storage(
        discover_true_achievements(session, slug, as_of_game_date=as_of_game_date),
        season_label or "",
    )
    pairs = {(tid, key) for tid, keys in truths.items() for key in keys}
    as_of_max = int(
        session.scalar(
            select(func.coalesce(func.max(Game.id), 0)).where(
                Game.status == "final",
                Game.game_date <= as_of_game_date,
            )
        )
        or 0
    )
    watermark = session.scalar(
        select(GmAchievementWatermark).where(GmAchievementWatermark.league_slug == slug).limit(1)
    )
    if watermark is None:
        session.add(
            GmAchievementWatermark(
                league_slug=slug,
                max_game_id=as_of_max,
                season_label=season_label or "",
                already_true_json=_pairs_to_json(pairs),
                tenure_json="{}",
                team_tiers_json=json.dumps(_team_tiers_now(session, slug)),
                evaluated_at=datetime.utcnow(),
            )
        )
    else:
        watermark.max_game_id = as_of_max
        watermark.season_label = season_label or watermark.season_label
        watermark.already_true_json = _pairs_to_json(pairs)
        watermark.team_tiers_json = json.dumps(_team_tiers_now(session, slug))
        watermark.evaluated_at = datetime.utcnow()
    commit_with_sqlite_retry(session)
    stats["reseeds"] = 1
    _log.info(
        "GM achievements watermark reseeded for %s as of %s (%s already-true pairs).",
        slug,
        as_of_game_date.isoformat(),
        len(pairs),
    )
    awarded = evaluate_gm_achievements_after_import(app)
    stats.update(awarded)
    stats["reseeds"] = 1
    return stats


def revoke_gm_achievement_unlocks(
    app,
    pairs: Iterable[tuple[int, str]],
) -> dict[str, int]:
    """Delete unclaimed unlocks and drop those pairs from the watermark so they can be earned later."""
    slug = str(getattr(app, "config", {}).get("LEAGUE_SLUG") or "").strip()
    stats = {"revoked": 0, "skipped_claimed": 0, "watermark_removed": 0, "skipped": 0}
    wanted = {(int(tid), str(key)) for tid, key in pairs}
    if slug not in HOCKEY_SLUGS or not wanted:
        stats["skipped"] = 1
        return stats

    from app.league_db import db
    from app.sqlite_retry import commit_with_sqlite_retry

    session = db.session
    unlocks = list(
        session.scalars(select(GmAchievementUnlock).where(GmAchievementUnlock.league_slug == slug)).all()
    )
    for unlock in unlocks:
        pair = (int(unlock.team_id), str(unlock.achievement_key))
        if pair not in wanted:
            continue
        if unlock.claimed_at is not None or int(unlock.ap_delta or 0) > 0:
            stats["skipped_claimed"] += 1
            continue
        session.delete(unlock)
        stats["revoked"] += 1

    watermark = session.scalar(
        select(GmAchievementWatermark).where(GmAchievementWatermark.league_slug == slug).limit(1)
    )
    if watermark is not None:
        kept = {pair for pair in _already_pairs(watermark) if pair not in wanted}
        removed = len(_already_pairs(watermark)) - len(kept)
        if removed:
            watermark.already_true_json = _pairs_to_json(kept)
            watermark.evaluated_at = datetime.utcnow()
            stats["watermark_removed"] = removed

    commit_with_sqlite_retry(session)
    _log.info(
        "GM achievements revoked for %s: %s unlocks, %s watermark pairs.",
        slug,
        stats["revoked"],
        stats["watermark_removed"],
    )
    return stats


def evaluate_gm_achievements_after_import(app) -> dict[str, int]:
    """Seed a watermark on first import; award new unlocks after that."""
    slug = str(getattr(app, "config", {}).get("LEAGUE_SLUG") or "").strip()
    stats = {"seeded": 0, "awarded": 0, "skipped": 0, "ledger_synced": 0}
    if slug not in HOCKEY_SLUGS:
        stats["skipped"] = 1
        return stats

    from app.league_db import db
    from app.sqlite_retry import commit_with_sqlite_retry

    session = db.session
    watermark = session.scalar(
        select(GmAchievementWatermark).where(GmAchievementWatermark.league_slug == slug).limit(1)
    )
    season = get_current_season()
    season_label = season_display_label(season)
    max_gid = _max_game_id(session)
    tiers_now = _team_tiers_now(session, slug)
    memberships = _active_memberships(session, slug)

    if watermark is None:
        truths = rewrite_truths_to_storage(discover_true_achievements(session, slug), season_label or "")
        pairs = {(tid, key) for tid, keys in truths.items() for key in keys}
        row = GmAchievementWatermark(
            league_slug=slug,
            max_game_id=max_gid,
            season_label=season_label or "",
            already_true_json=_pairs_to_json(pairs),
            tenure_json="{}",
            team_tiers_json=json.dumps(tiers_now),
            evaluated_at=datetime.utcnow(),
        )
        session.add(row)
        commit_with_sqlite_retry(session)
        stats["seeded"] = 1
        _log.info("GM achievements watermark seeded for %s (%s already-true pairs).", slug, len(pairs))
        return stats

    tenure = watermark.tenure_map()
    for mem in memberships.values():
        tkey = f"{int(mem.user_id)}:{int(mem.team_id)}"
        seasons = list(tenure.get(tkey) or [])
        if season_label and season_label not in seasons:
            seasons.append(season_label)
            tenure[tkey] = seasons
    tenure_counts = {int(mem.team_id): len(tenure.get(f"{int(mem.user_id)}:{int(mem.team_id)}") or []) for mem in memberships.values()}
    promoted = _promoted_ids(watermark.team_tiers_map(), tiers_now)

    truths = rewrite_truths_to_storage(
        discover_true_achievements(
            session,
            slug,
            tenure_counts=tenure_counts,
            promoted_team_ids=promoted,
        ),
        season_label or "",
    )
    already = expand_legacy_pairs(_already_pairs(watermark), season_label or "")
    existing = expand_legacy_pairs(
        {
            (int(r.team_id), str(r.achievement_key))
            for r in session.scalars(
                select(GmAchievementUnlock).where(GmAchievementUnlock.league_slug == slug)
            ).all()
        },
        season_label or "",
    )

    awarded = 0
    recap_by_user: dict[int, dict[str, Any]] = {}
    for team_id, key, meta in collect_new_hits(truths, already, existing):
        spec = CATALOG_BY_KEY.get(catalog_key_from_storage(key))
        if spec is None:
            continue
        pair = (int(team_id), key)
        mem = memberships.get(int(team_id))
        user_id = int(mem.user_id) if mem else None
        source_ref = unlock_source_ref(slug, team_id, key)
        unlock = GmAchievementUnlock(
            league_slug=slug,
            team_id=int(team_id),
            user_id=user_id,
            achievement_key=key,
            source_ref=source_ref,
            unlocked_at=datetime.utcnow(),
            season_label=season_label or "",
            meta_json=json.dumps(meta or {}),
            ap_delta=0,
        )
        session.add(unlock)
        if user_id is not None:
            _enqueue_achievement_discord(
                session,
                league_slug=slug,
                team_id=int(team_id),
                spec=spec,
                meta=meta or {},
                source_ref=source_ref,
                season_label=season_label or "",
            )
            bucket = recap_by_user.setdefault(user_id, {"team_id": int(team_id), "titles": []})
            bucket["titles"].append(spec.title)
        already.add(pair)
        existing.add(pair)
        awarded += 1

    synced = sync_achievement_ap_ledger(session, slug)
    stats["ledger_synced"] = synced

    if recap_by_user:
        _enqueue_export_recaps(
            session,
            league_slug=slug,
            recap_by_user=recap_by_user,
            max_gid=max_gid,
        )

    watermark.max_game_id = max(int(watermark.max_game_id or 0), max_gid)
    watermark.season_label = season_label or watermark.season_label
    watermark.already_true_json = _pairs_to_json(already)
    watermark.tenure_json = json.dumps(tenure)
    watermark.team_tiers_json = json.dumps(tiers_now)
    watermark.evaluated_at = datetime.utcnow()
    commit_with_sqlite_retry(session)
    stats["awarded"] = awarded
    if awarded:
        _log.info("GM achievements awarded %s unlock(s) for %s.", awarded, slug)
    return stats


def _enqueue_achievement_discord(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    spec: AchievementDef,
    meta: dict[str, Any],
    source_ref: str,
    season_label: str,
) -> None:
    try:
        from app.services.discord_events import (
            build_league_public_url,
            enqueue_discord_event,
            is_discord_event_route_active,
        )
    except Exception:
        return
    event_key = ACHIEVEMENT_LEAGUE_FIRST_EVENT_KEY if spec.race else ACHIEVEMENT_UNLOCKED_EVENT_KEY
    if not is_discord_event_route_active(session, league_slug=league_slug, event_key=event_key):
        if spec.race and is_discord_event_route_active(
            session, league_slug=league_slug, event_key=ACHIEVEMENT_UNLOCKED_EVENT_KEY
        ):
            event_key = ACHIEVEMENT_UNLOCKED_EVENT_KEY
        else:
            return
    team = session.get(Team, int(team_id))
    detail = str(meta.get("detail") or "").strip()
    payload = {
        "title": spec.title,
        "message": detail or spec.description,
        "achievement_title": spec.title,
        "detail": detail or spec.description,
        "ap_delta": spec.ap,
        "season_label": season_label,
        "is_race": bool(spec.race),
        "team_id": team_id,
        "team_abbrev": (team.abbreviation if team else "") or "",
        "team_name": team.full_display_name() if team else "",
        "url": build_league_public_url(league_slug, "/achievement-leaders") or "",
    }
    enqueue_discord_event(
        session,
        league_slug=league_slug,
        event_key=event_key,
        payload=payload,
        created_by_user_id=None,
        source_type="gm_achievement",
        source_id=source_ref,
    )


def _enqueue_export_recaps(
    session: Session,
    *,
    league_slug: str,
    recap_by_user: dict[int, dict[str, Any]],
    max_gid: int,
) -> None:
    try:
        from app.services.discord_direct_messages import enqueue_direct_message
        from app.services.discord_events import build_league_public_url
    except Exception:
        return
    board = build_achievement_leaderboard(session, league_slug)
    by_team = {int(row["team_id"]): row for row in board.get("rows") or []}
    url = build_league_public_url(league_slug, "/achievement-leaders") or ""
    for user_id, rec in recap_by_user.items():
        titles = list(rec.get("titles") or [])
        if not titles:
            continue
        team_id = int(rec.get("team_id") or 0)
        standing = by_team.get(team_id) or {}
        rank = standing.get("rank")
        title, body = format_export_recap(titles=titles, rank=int(rank) if rank else None)
        try:
            enqueue_direct_message(
                session,
                league_slug=league_slug,
                recipient_user_id=int(user_id),
                event_key=ACHIEVEMENT_EXPORT_RECAP_EVENT_KEY,
                title=title,
                body=body,
                source_type="gm_achievement_export",
                source_id=f"{int(max_gid)}:{team_id}:{int(user_id)}",
                url=url,
                preview=body,
            )
        except Exception:
            _log.exception("GM achievements: export recap DM failed for user %s.", user_id)


def progress_for_team(
    session: Session,
    league_slug: str,
    team_id: int,
    watermark: GmAchievementWatermark | None,
    membership: GmLeagueMembership | None,
) -> dict[str, dict[str, Any]]:
    """In-progress fractions for the Achievements page."""
    out: dict[str, dict[str, Any]] = {}
    season = get_current_season()
    if membership and watermark:
        tkey = f"{int(membership.user_id)}:{int(team_id)}"
        n = len(watermark.tenure_map().get(tkey) or [])
        out["true_franchise"] = {"current": n, "target": 10, "label": f"{n} / 10 seasons"}
    recs = list(
        session.scalars(select(TeamSeasonRecord).where(TeamSeasonRecord.team_id == int(team_id))).all()
    )
    recs.sort(key=lambda r: (int(r.start_year or 0), r.season_year_label or ""))
    streak = 0
    prev = None
    for rec in recs:
        if rec.start_year and _is_champion_result(rec.result):
            if prev is not None and int(rec.start_year) == prev + 1:
                streak += 1
            else:
                streak = 1
            prev = int(rec.start_year)
        else:
            streak = 0
            prev = int(rec.start_year) if rec.start_year else prev
    out["dynasty"] = {"current": streak, "target": 5, "label": f"{streak} / 5 consecutive cups"}

    if season:
        skaters = list(
            session.scalars(
                select(PlayerSkaterStat).where(
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.team_id == int(team_id),
                    PlayerSkaterStat.stat_segment == "rs",
                )
            ).all()
        )
        best_g = max((int(s.goals or 0) for s in skaters), default=0)
        best_gp = 0
        for s in skaters:
            if int(s.goals or 0) == best_g:
                best_gp = int(s.gp or 0)
        if best_g:
            out["rocket"] = {
                "current": min(best_g, 50),
                "target": 50,
                "label": f"{best_g} G in {best_gp} GP",
            }
        standings = list(
            session.scalars(select(TeamStanding).where(TeamStanding.season_id == season.id)).all()
        )
        ranks = _rs_ranks(standings)
        rank = ranks.get(int(team_id))
        if rank:
            out["presidents"] = {
                "current": 1 if rank == 1 else 0,
                "target": 1,
                "label": f"#{rank} in the league",
            }
        team_games = [
            g
            for g in session.scalars(
                select(Game).where(
                    Game.status == "final",
                    Game.season_id == season.id,
                    or_(Game.home_team_id == int(team_id), Game.away_team_id == int(team_id)),
                )
            ).all()
        ]
        team_games.sort(key=lambda g: (g.game_date or date.min, int(g.id)))
        letters = []
        for g in team_games:
            if not is_regular_season_game_type(g.game_type):
                continue
            letter = game_outcome_letter(g, int(team_id))
            if letter:
                letters.append(letter)
        heater = max_win_streak(letters)
        if heater:
            out["on_a_heater"] = {
                "current": min(heater, WIN_STREAK_TARGET),
                "target": WIN_STREAK_TARGET,
                "label": f"{heater}-game win streak",
            }
        ot_n = 0
        wins_vs: dict[int, int] = {}
        for g in team_games:
            winner_id, loser_id = _game_winner_loser(g)
            if winner_id != int(team_id) or loser_id is None:
                continue
            if not is_playoff_game_type(g.game_type) and (g.went_to_overtime or g.went_to_shootout):
                ot_n += 1
            wins_vs[int(loser_id)] = wins_vs.get(int(loser_id), 0) + 1
        if ot_n:
            out["overtime_merchant"] = {
                "current": min(ot_n, OT_WINS_TARGET),
                "target": OT_WINS_TARGET,
                "label": f"{ot_n} OT/SO wins",
            }
        if wins_vs:
            best_vs = max(wins_vs.values())
            out["nemesis"] = {
                "current": min(best_vs, NEMESIS_WINS_TARGET),
                "target": NEMESIS_WINS_TARGET,
                "label": f"{best_vs} wins vs one opponent",
            }
        star_counts: dict[int, int] = {}
        star_game_ids = [int(g.id) for g in team_games if getattr(g, "fhm_star1_player_id", None)]
        star_on_team: set[tuple[int, int]] = set()
        if star_game_ids:
            for ln in session.scalars(
                select(GameSkaterStat).where(
                    GameSkaterStat.game_id.in_(star_game_ids),
                    GameSkaterStat.team_id == int(team_id),
                )
            ).all():
                if ln.player_id:
                    star_on_team.add((int(ln.game_id), int(ln.player_id)))
            for ln in session.scalars(
                select(GameGoalieStat).where(
                    GameGoalieStat.game_id.in_(star_game_ids),
                    GameGoalieStat.team_id == int(team_id),
                )
            ).all():
                if ln.player_id:
                    star_on_team.add((int(ln.game_id), int(ln.player_id)))
        for g in team_games:
            star_pid = getattr(g, "fhm_star1_player_id", None)
            if star_pid and (int(g.id), int(star_pid)) in star_on_team:
                star_counts[int(star_pid)] = star_counts.get(int(star_pid), 0) + 1
        if star_counts:
            best_stars = max(star_counts.values())
            out["three_star_season"] = {
                "current": min(best_stars, FIRST_STAR_TARGET),
                "target": FIRST_STAR_TARGET,
                "label": f"{best_stars} first-star games",
            }
        cheap_goals = 0
        skater_pids = [int(st.player_id) for st in skaters if st.player_id]
        contracts_by_pid: dict[int, PlayerContract] = {}
        if skater_pids:
            for contract in session.scalars(
                select(PlayerContract).where(PlayerContract.player_id.in_(skater_pids))
            ).all():
                if contract.player_id:
                    contracts_by_pid[int(contract.player_id)] = contract
        for st in skaters:
            if not st.player_id:
                continue
            contract = contracts_by_pid.get(int(st.player_id))
            salary = int(contract.average_salary) if contract and contract.average_salary is not None else None
            if salary is None or salary <= 0 or salary >= BARGAIN_BIN_SALARY_CAP:
                continue
            cheap_goals = max(cheap_goals, int(st.goals or 0))
        if cheap_goals:
            out["bargain_bin"] = {
                "current": min(cheap_goals, 30),
                "target": 30,
                "label": f"{cheap_goals} goals on a sub-$1M deal",
            }
        elc_goals = 0
        for st in skaters:
            if not st.player_id:
                continue
            contract = contracts_by_pid.get(int(st.player_id))
            if contract and contract.is_elc:
                elc_goals = max(elc_goals, int(st.goals or 0))
        if elc_goals:
            out["elc_lightning"] = {
                "current": min(elc_goals, ELC_GOALS_TARGET),
                "target": ELC_GOALS_TARGET,
                "label": f"{elc_goals} ELC goals",
            }
        try:
            acquired = _acquired_players_from_published_trades(session, league_slug)
        except Exception:
            acquired = {}
        heist_best = 0
        for st in skaters:
            if not st.player_id or not st.team_id:
                continue
            if int(st.player_id) not in acquired.get(int(st.team_id), set()):
                continue
            heist_best = max(heist_best, int(st.points or 0))
        if heist_best:
            out["the_heist"] = {
                "current": min(heist_best, HEIST_POINTS_TARGET),
                "target": HEIST_POINTS_TARGET,
                "label": f"{heist_best} points from a Trade Tool acquisition",
            }
    drafted_ids = {
        int(p.player_id)
        for p in session.scalars(
            select(DraftPick).where(DraftPick.team_id == int(team_id), DraftPick.player_id.is_not(None))
        ).all()
        if p.player_id
    }
    if drafted_ids:
        grown = session.scalar(
            select(func.count(Player.id)).where(
                Player.current_team_id == int(team_id),
                Player.retired.is_(False),
                Player.id.in_(drafted_ids),
            )
        )
        grown_n = int(grown or 0)
        if grown_n:
            out["homegrown_core"] = {
                "current": min(grown_n, HOMEGROWN_CORE_TARGET),
                "target": HOMEGROWN_CORE_TARGET,
                "label": f"{grown_n} / {HOMEGROWN_CORE_TARGET} self-drafted",
            }
    export_dates = [
        row.export_date
        for row in session.scalars(
            select(GmExportAttendance).where(
                GmExportAttendance.league_slug == league_slug,
                GmExportAttendance.team_id == int(team_id),
            )
        ).all()
        if row.export_date
    ]
    export_n = export_streak_len(export_dates)
    if export_n:
        out["export_streak"] = {
            "current": min(export_n, EXPORT_STREAK_TARGET),
            "target": EXPORT_STREAK_TARGET,
            "label": f"{export_n} / {EXPORT_STREAK_TARGET} consecutive exports",
        }
    playoff_years = sorted(
        {
            int(r.start_year)
            for r in recs
            if r.start_year and r.result and not _missed_playoffs(r.result)
        }
    )
    po_streak = 0
    prev_y = None
    for y in playoff_years:
        po_streak = po_streak + 1 if prev_y is not None and y == prev_y + 1 else 1
        prev_y = y
    if po_streak:
        out["iron_decade"] = {
            "current": min(po_streak, IRON_DECADE_TARGET),
            "target": IRON_DECADE_TARGET,
            "label": f"{po_streak} / {IRON_DECADE_TARGET} consecutive playoff years",
        }
    try:
        from app.services.export_attendance import ATTENDANCE_WINDOW_DAYS, rolling_attendance_window_dates
    except Exception:
        rolling_attendance_window_dates = None
        ATTENDANCE_WINDOW_DAYS = 45
    if rolling_attendance_window_dates is not None:
        window = set(rolling_attendance_window_dates())
        league_days = {
            row.export_date
            for row in session.scalars(
                select(GmExportAttendance).where(GmExportAttendance.league_slug == league_slug)
            ).all()
            if row.export_date and row.export_date in window
        }
        team_days = {d for d in export_dates if d in window}
        if league_days:
            out["perfect_attendance"] = {
                "current": len(team_days & league_days),
                "target": len(league_days),
                "label": f"{len(team_days & league_days)} / {len(league_days)} exports in {ATTENDANCE_WINDOW_DAYS} days",
            }
    return out


def build_achievements_page_payload(
    session: Session,
    *,
    league_slug: str,
    membership: GmLeagueMembership | None,
) -> dict[str, Any]:
    team_id = int(membership.team_id) if membership else None
    team = session.get(Team, team_id) if team_id else None
    watermark = session.scalar(
        select(GmAchievementWatermark).where(GmAchievementWatermark.league_slug == league_slug).limit(1)
    )
    season = get_current_season()
    season_label = season_display_label(season)
    current_period = datetime.utcnow().date().strftime("%Y-%m")
    unlocks: dict[str, GmAchievementUnlock] = {}
    unlock_rows: list[GmAchievementUnlock] = []
    if team_id:
        unlock_rows = list(
            session.scalars(
                select(GmAchievementUnlock).where(
                    GmAchievementUnlock.league_slug == league_slug,
                    GmAchievementUnlock.team_id == team_id,
                )
            ).all()
        )
        for row in unlock_rows:
            unlocks[str(row.achievement_key)] = row
    progress = progress_for_team(session, league_slug, team_id, watermark, membership) if team_id else {}
    groups: dict[str, list[dict[str, Any]]] = {"game": [], "season": [], "playoffs": [], "career": []}
    completed = 0
    total_ap = sum(int(row.ap_delta or 0) for row in unlock_rows if row.claimed_at is not None)
    for spec in catalog_for_league(league_slug):
        period = current_period if spec.repeat_scope == "month" else None
        store = storage_key_for(spec, season_label or "", period)
        unlock = unlocks.get(store) or unlocks.get(spec.key)
        prior = [
            row
            for row in unlock_rows
            if catalog_key_from_storage(row.achievement_key) == spec.key
        ]
        prog = progress.get(spec.key)
        status = "locked"
        blurb = ""
        hidden_locked = bool(spec.hidden)
        title = spec.title
        description = spec.description
        if unlock:
            status = "completed"
            completed += 1
            blurb = str((unlock.meta_map() or {}).get("detail") or "")
            if unlock.season_label:
                blurb = f"{blurb} · {unlock.season_label}".strip(" ·")
            if spec.repeatable and len(prior) > 1:
                blurb = f"{blurb} · {len(prior)}x".strip(" ·")
            hidden_locked = False
        elif spec.hidden:
            title = "???"
            description = "Hidden achievement. Keep playing."
            prog = None
        elif prog and int(prog.get("current") or 0) > 0:
            status = "progress"
            blurb = str(prog.get("label") or "")
        claimed = bool(unlock and unlock.claimed_at)
        cells = parse_reward_cells(unlock.reward_cells_json) if unlock else None
        ticket_ap = int(unlock.reward_ticket_ap) if unlock and unlock.reward_ticket_ap is not None else None
        groups.setdefault(spec.category, []).append(
            {
                "key": spec.key,
                "storage_key": str(unlock.achievement_key) if unlock else store,
                "title": title,
                "description": description,
                "ap": spec.ap,
                "multiplier": spec.ap,
                "category": spec.category,
                "status": status,
                "blurb": blurb,
                "progress": prog,
                "hidden": hidden_locked,
                "repeatable": spec.repeatable,
                "unlocked_at": unlock.unlocked_at if unlock else None,
                "claimed": claimed,
                "claimable": status == "completed" and not claimed,
                "reward_cells": cells,
                "reward_ticket_ap": ticket_ap,
                "reward_total": int(unlock.ap_delta or 0) if claimed else None,
            }
        )
    items = [card for cards in groups.values() for card in cards]
    return {
        "team": team,
        "groups": groups,
        "items": items,
        "completed": completed,
        "total": len(items),
        "total_ap": total_ap,
        "watermarked": watermark is not None,
    }


def team_achievement_badges(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
) -> list[dict[str, Any]]:
    """Heritage cups plus post-ship unlocks for the team page banner."""
    badges: list[dict[str, Any]] = []
    recs = list(
        session.scalars(select(TeamSeasonRecord).where(TeamSeasonRecord.team_id == int(team_id))).all()
    )
    cup_years = [r.season_year_label for r in recs if _is_champion_result(r.result)]
    hist = list(
        session.scalars(select(HistoryChampion).where(HistoryChampion.team_id == int(team_id))).all()
    )
    cup_count = max(len(cup_years), len(hist)) if (cup_years or hist) else 0
    if cup_years and hist:
        cup_count = max(len(set(cup_years)), len(hist))
    if cup_count:
        badges.append(
            {
                "key": "heritage_cup",
                "title": "BOWL Cup",
                "category": "playoffs",
                "count": cup_count,
                "tooltip": f"{cup_count}x BOWL Cup",
                "heritage": True,
            }
        )
    recs.sort(key=lambda r: (int(r.start_year or 0), r.season_year_label or ""))
    streak = best = 0
    prev = None
    for rec in recs:
        if rec.start_year and _is_champion_result(rec.result):
            streak = streak + 1 if prev is not None and int(rec.start_year) == prev + 1 else 1
            best = max(best, streak)
            prev = int(rec.start_year)
        elif rec.start_year:
            streak = 0
            prev = int(rec.start_year)
    if best >= 5:
        badges.append(
            {
                "key": "heritage_dynasty",
                "title": "Dynasty",
                "category": "career",
                "count": best,
                "tooltip": f"{best} consecutive championships",
                "heritage": True,
            }
        )

    seen = {b["key"] for b in badges}
    for row in session.scalars(
        select(GmAchievementUnlock).where(
            GmAchievementUnlock.league_slug == league_slug,
            GmAchievementUnlock.team_id == int(team_id),
        )
    ).all():
        spec = CATALOG_BY_KEY.get(catalog_key_from_storage(row.achievement_key))
        if spec is None:
            continue
        if spec.key in {"pinnacle", "dynasty"} and (
            "heritage_cup" in seen or "heritage_dynasty" in seen
        ):
            continue
        existing = next((b for b in badges if b["key"] == spec.key), None)
        if existing:
            existing["count"] = int(existing.get("count") or 1) + 1
            existing["tooltip"] = f"{spec.title} · {existing['count']}x"
            continue
        badges.append(
            {
                "key": spec.key,
                "title": spec.title,
                "category": spec.category,
                "count": 1,
                "tooltip": f"{spec.title}" + (f" · {row.season_label}" if row.season_label else ""),
                "heritage": False,
            }
        )
    return badges


def _gm_names_for_memberships(
    session: Session, memberships: dict[int, GmLeagueMembership]
) -> dict[int, str]:
    user_ids = {int(m.user_id) for m in memberships.values() if m.user_id}
    users: dict[int, str] = {}
    if user_ids:
        from app.services.gm_messaging import gm_display_name

        for user in session.scalars(select(User).where(User.id.in_(user_ids))).all():
            users[int(user.id)] = gm_display_name(user)
    return users


def _places_by_catalog_key(
    rows: list[GmAchievementUnlock],
    teams: dict[int, Team],
    memberships: dict[int, GmLeagueMembership],
    users: dict[int, str],
) -> dict[str, list[dict[str, Any]]]:
    places: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, int]] = set()
    ordered = sorted(
        rows,
        key=lambda r: (r.unlocked_at or datetime.max, int(getattr(r, "id", 0) or 0)),
    )
    for row in ordered:
        cat = catalog_key_from_storage(row.achievement_key)
        if CATALOG_BY_KEY.get(cat) is None:
            continue
        pair = (cat, int(row.team_id))
        if pair in seen:
            continue
        seen.add(pair)
        team = teams.get(int(row.team_id))
        mem = memberships.get(int(row.team_id))
        places.setdefault(cat, []).append(
            {
                "team_id": int(row.team_id),
                "team_name": team.full_display_name() if team else f"Team {row.team_id}",
                "team_slug": (team.slug if team else "") or "",
                "team_abbrev": (team.abbreviation if team else "") or "",
                "gm_name": users.get(int(mem.user_id)) if mem and mem.user_id else "",
                "unlocked_at": row.unlocked_at,
            }
        )
    return places


def build_achievement_leaderboard(session: Session, league_slug: str) -> dict[str, Any]:
    """Public trophy standings: AP earned, unlock count, and first-to-unlock races."""
    rows = list(
        session.scalars(select(GmAchievementUnlock).where(GmAchievementUnlock.league_slug == league_slug)).all()
    )
    by_team: dict[int, dict[str, Any]] = {}
    for row in rows:
        rec = by_team.setdefault(
            int(row.team_id),
            {"team_id": int(row.team_id), "unlocks": 0, "ap": 0, "latest": row.unlocked_at},
        )
        rec["unlocks"] += 1
        rec["ap"] += int(row.ap_delta or 0)
        if row.unlocked_at and (rec["latest"] is None or row.unlocked_at > rec["latest"]):
            rec["latest"] = row.unlocked_at
    teams = {int(t.id): t for t in session.scalars(select(Team)).all()}
    memberships = _active_memberships(session, league_slug)
    users = _gm_names_for_memberships(session, memberships)
    board: list[dict[str, Any]] = []
    for tid, rec in by_team.items():
        team = teams.get(tid)
        mem = memberships.get(tid)
        board.append(
            {
                "team_id": tid,
                "team_name": team.full_display_name() if team else f"Team {tid}",
                "team_slug": (team.slug if team else "") or "",
                "team_abbrev": (team.abbreviation if team else "") or "",
                "gm_name": users.get(int(mem.user_id)) if mem and mem.user_id else "",
                "unlocks": rec["unlocks"],
                "ap": rec["ap"],
                "latest": rec["latest"],
            }
        )
    board.sort(key=lambda r: (-int(r["ap"]), -int(r["unlocks"]), str(r["team_name"])))
    for i, rec in enumerate(board, start=1):
        rec["rank"] = i
    places = _places_by_catalog_key(rows, teams, memberships, users)
    firsts: list[dict[str, Any]] = []
    for spec in catalog_for_league(league_slug):
        if spec.hidden:
            continue
        order = places.get(spec.key) or []
        if not order:
            continue
        first = dict(order[0])
        first["key"] = spec.key
        first["title"] = spec.title
        first["category"] = spec.category
        firsts.append(first)
    return {
        "rows": board,
        "firsts": firsts,
        "places": places,
        "total_unlocks": len(rows),
        "total_ap": sum(int(r.ap_delta or 0) for r in rows),
    }


def build_achievement_rival_page(session: Session, league_slug: str, team_id: int) -> dict[str, Any] | None:
    """Per-franchise trophy case vs who unlocked each badge first."""
    team = session.get(Team, int(team_id))
    if team is None:
        return None
    board = build_achievement_leaderboard(session, league_slug)
    standing = next((row for row in board["rows"] if int(row["team_id"]) == int(team_id)), None)
    places: dict[str, list[dict[str, Any]]] = board.get("places") or {}
    races: list[dict[str, Any]] = []
    for spec in catalog_for_league(league_slug):
        order = places.get(spec.key) or []
        my_place = next((i for i, entry in enumerate(order, start=1) if int(entry["team_id"]) == int(team_id)), None)
        if spec.hidden and my_place is None:
            continue
        first = order[0] if order else None
        races.append(
            {
                "key": spec.key,
                "title": spec.title,
                "category": spec.category,
                "hidden": spec.hidden,
                "place": my_place,
                "place_label": place_label(my_place),
                "is_first": my_place == 1,
                "first_team_name": (first or {}).get("team_name") or "",
                "first_team_slug": (first or {}).get("team_slug") or "",
                "first_gm_name": (first or {}).get("gm_name") or "",
                "first_at": (first or {}).get("unlocked_at"),
            }
        )
    return {
        "team": team,
        "team_name": team.full_display_name(),
        "standing": standing,
        "badges": team_achievement_badges(session, league_slug=league_slug, team_id=int(team_id)),
        "races": races,
        "rows": board["rows"],
        "firsts": board["firsts"],
        "total_unlocks": board["total_unlocks"],
        "total_ap": board["total_ap"],
    }
