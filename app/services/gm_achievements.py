"""GM achievements: catalog, detectors, post-import evaluation, and page payloads."""
from __future__ import annotations

import json
import logging
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
    HistoryAward,
    HistoryChampion,
    PenaltyEvent,
    Player,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    ScoringEvent,
    Team,
    TeamSeasonRecord,
    TeamStanding,
)
from app.services.playoff_bracket import is_playoff_game_type
from app.services.seasons import get_current_season, season_display_label
from app.services.team_records import CHAMPION_RESULT
from app.site_models import GmAchievementUnlock, GmAchievementWatermark, GmLeagueMembership

_log = logging.getLogger(__name__)

ACHIEVEMENT_UNLOCKED_EVENT_KEY = "achievement_unlocked"
REASON_CODE = "gm_achievement"

HOCKEY_SLUGS = frozenset(HOCKEY_LEAGUE_SLUGS)
RELEGATION_ONLY = frozenset({"bowl-fantasy"})

_FIGHT_WORDS = ("fight", "fighting", "fisticuffs", "combat")
_MVP_NEEDLES = ("HART", "MOST VALUABLE", "MVP")
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


CATALOG: tuple[AchievementDef, ...] = (
    AchievementDef("gordie_howe", "Gordie Howe Hat Trick", "One of your players has a goal, assist, and fight in the same game.", "game", 1),
    AchievementDef("all_natural", "All Natural", "One of your players scores a natural hat trick.", "game", 1),
    AchievementDef("goalie_win", "A Goalie Win", "Win a game 1–0 despite giving up 40 or more shots.", "game", 1),
    AchievementDef("nationalism", "Nationalism", "Play a game with a lineup composed entirely of players from the nation your team is based in.", "game", 2),
    AchievementDef("make_playoffs", "Kiss from a Rose", "Make the playoffs.", "playoffs", 1),
    AchievementDef("senior_team", "The Senior Team", "Have at least 3 players on your roster over the age of 40.", "season", 1),
    AchievementDef("game54", "game54", "Draft a player with the 54th overall pick.", "career", 1),
    AchievementDef("hometown_roster", "Hometown Roster", "Have at least three players on your roster born in your team's city.", "season", 2),
    AchievementDef("upset", "Upset!", "Win a playoff series against a team that finished ahead of you in the regular season.", "playoffs", 2),
    AchievementDef("guarantee", "The Guarantee", "Win a 7-game playoff series after being down 3–2.", "playoffs", 2),
    AchievementDef("road_warrior", "Road Warrior", "Lead the league in road wins.", "season", 2),
    AchievementDef("youre_special", "You're Special", "Finish first in the league in both power play and penalty killing percentage.", "season", 2),
    AchievementDef("new_team_mvp", "New Team, Who's This?", "Have a player win the league MVP in their first season on your team.", "season", 2),
    AchievementDef("pinnacle", "The Pinnacle", "Win the BOWL Cup.", "playoffs", 3),
    AchievementDef("rocket", "Outracing the Rocket", "One of your players scores 50 goals in 50 or fewer games.", "season", 3),
    AchievementDef("zero_to_hero", "Zero to Hero", "Win the championship after finishing last overall the previous season.", "playoffs", 3),
    AchievementDef("going_up", "Going Up", "Your team is promoted to a higher league.", "career", 3, RELEGATION_ONLY),
    AchievementDef("presidents", "Presidents' Club", "Finish first in the league in the regular season.", "season", 3),
    AchievementDef("playoff_sweep", "Playoff Sweep", "Sweep a playoff series.", "playoffs", 3),
    AchievementDef("century_club", "100-Point Skater", "One of your players records 100 points in a season.", "season", 3),
    AchievementDef("fifty_goals", "50-Goal Skater", "One of your players scores 50 goals in a season.", "season", 3),
    AchievementDef("forty_win_goalie", "40-Win Goalie", "One of your goalies wins 40 games in a season.", "season", 3),
    AchievementDef("dynasty", "A Real Dynasty", "Win the league championship five consecutive times.", "career", 5),
    AchievementDef("two_hundred", "The 200 Club", "One of your players scores 200 points in a season.", "season", 5),
    AchievementDef("true_franchise", "True Franchise Manager", "Manage a single team for ten seasons.", "career", 5),
    AchievementDef("cup_eight_seed", "Cup as 8-seed", "Win the BOWL Cup as the lowest playoff seed.", "playoffs", 5),
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


def discover_true_achievements(
    session: Session,
    league_slug: str,
    *,
    tenure_counts: dict[int, int] | None = None,
    promoted_team_ids: set[int] | None = None,
) -> dict[int, dict[str, dict[str, Any]]]:
    """Return team_id -> {achievement_key: meta} for currently true achievements."""
    allowed = {item.key for item in catalog_for_league(league_slug)}
    hits: dict[int, dict[str, dict[str, Any]]] = {}

    def mark(team_id: int | None, key: str, meta: dict[str, Any] | None = None) -> None:
        if not team_id or key not in allowed:
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
    for pen in penalties:
        if pen.player_id and is_fighting_infraction(pen.infraction):
            fights_by_game_player.add((int(pen.game_id), int(pen.player_id)))

    scoring_by_game: dict[int, list[ScoringEvent]] = {}
    for ev in scoring:
        scoring_by_game.setdefault(int(ev.game_id), []).append(ev)

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

    # --- season counting stats ---
    if season:
        skaters = list(
            session.scalars(
                select(PlayerSkaterStat).where(
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.stat_segment == "rs",
                )
            ).all()
        )
        goalies = list(
            session.scalars(
                select(PlayerGoalieStat).where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == "rs",
                )
            ).all()
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
        if year_recs:
            best_pp = max(float(r.pp_pct or 0) for r in year_recs)
            best_pk = max(float(r.pk_pct or 0) for r in year_recs)
            for r in year_recs:
                if r.team_id and float(r.pp_pct or 0) == best_pp and float(r.pk_pct or 0) == best_pk:
                    mark(r.team_id, "youre_special", {"detail": "Led the league in PP% and PK%"})

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
        if ser.winner_id and ser.winner_id in champ_team_ids and ser.winner_is_lowest_seed(playoff_ranks):
            mark(ser.winner_id, "cup_eight_seed", {"detail": "Won the Cup as the lowest playoff seed"})

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

    if tenure_counts:
        for tid, n in tenure_counts.items():
            if n >= 10:
                mark(tid, "true_franchise", {"seasons": n, "detail": f"Managed the franchise for {n} seasons"})

    if promoted_team_ids:
        for tid in promoted_team_ids:
            mark(tid, "going_up", {"detail": "Promoted to the higher league"})

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


def evaluate_gm_achievements_after_import(app) -> dict[str, int]:
    """Seed a watermark on first import; award new unlocks after that."""
    slug = str(getattr(app, "config", {}).get("LEAGUE_SLUG") or "").strip()
    stats = {"seeded": 0, "awarded": 0, "skipped": 0}
    if slug not in HOCKEY_SLUGS:
        stats["skipped"] = 1
        return stats

    from app.league_db import db
    from app.services.ap_service import add_ledger_entry
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
        truths = discover_true_achievements(session, slug)
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

    truths = discover_true_achievements(
        session,
        slug,
        tenure_counts=tenure_counts,
        promoted_team_ids=promoted,
    )
    already = _already_pairs(watermark)
    existing = {
        (int(r.team_id), str(r.achievement_key))
        for r in session.scalars(
            select(GmAchievementUnlock).where(GmAchievementUnlock.league_slug == slug)
        ).all()
    }

    awarded = 0
    for team_id, key, meta in collect_new_hits(truths, already, existing):
        spec = CATALOG_BY_KEY.get(key)
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
            ap_delta=int(spec.ap),
        )
        session.add(unlock)
        if user_id is not None:
            add_ledger_entry(
                league_slug=slug,
                team_id=int(team_id),
                delta=int(spec.ap),
                reason_code=REASON_CODE,
                meta={
                    "achievement_key": key,
                    "achievement_title": spec.title,
                    "note": f"Achievement: {spec.title}",
                },
                created_by_user_id=user_id,
                source_ref=source_ref,
            )
            _enqueue_achievement_discord(
                session,
                league_slug=slug,
                team_id=int(team_id),
                spec=spec,
                meta=meta or {},
                source_ref=source_ref,
                season_label=season_label or "",
            )
        already.add(pair)
        existing.add(pair)
        awarded += 1

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
    if not is_discord_event_route_active(
        session, league_slug=league_slug, event_key=ACHIEVEMENT_UNLOCKED_EVENT_KEY
    ):
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
        "team_id": team_id,
        "team_abbrev": (team.abbreviation if team else "") or "",
        "team_name": team.full_display_name() if team else "",
        "url": build_league_public_url(league_slug, "/achievements") or "",
    }
    enqueue_discord_event(
        session,
        league_slug=league_slug,
        event_key=ACHIEVEMENT_UNLOCKED_EVENT_KEY,
        payload=payload,
        created_by_user_id=None,
        source_type="gm_achievement",
        source_id=source_ref,
    )


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
    unlocks: dict[str, GmAchievementUnlock] = {}
    if team_id:
        for row in session.scalars(
            select(GmAchievementUnlock).where(
                GmAchievementUnlock.league_slug == league_slug,
                GmAchievementUnlock.team_id == team_id,
            )
        ).all():
            unlocks[str(row.achievement_key)] = row
    progress = progress_for_team(session, league_slug, team_id, watermark, membership) if team_id else {}
    groups: dict[str, list[dict[str, Any]]] = {"game": [], "season": [], "playoffs": [], "career": []}
    completed = 0
    total_ap = 0
    for spec in catalog_for_league(league_slug):
        unlock = unlocks.get(spec.key)
        prog = progress.get(spec.key)
        status = "locked"
        blurb = ""
        if unlock:
            status = "completed"
            completed += 1
            total_ap += int(unlock.ap_delta or spec.ap)
            blurb = str((unlock.meta_map() or {}).get("detail") or "")
            if unlock.season_label:
                blurb = f"{blurb} · {unlock.season_label}".strip(" ·")
        elif prog and int(prog.get("current") or 0) > 0:
            status = "progress"
            blurb = str(prog.get("label") or "")
        groups.setdefault(spec.category, []).append(
            {
                "key": spec.key,
                "title": spec.title,
                "description": spec.description,
                "ap": spec.ap,
                "category": spec.category,
                "status": status,
                "blurb": blurb,
                "progress": prog,
                "unlocked_at": unlock.unlocked_at if unlock else None,
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
        spec = CATALOG_BY_KEY.get(row.achievement_key)
        if spec is None:
            continue
        if spec.key in {"pinnacle", "dynasty"} and (
            "heritage_cup" in seen or "heritage_dynasty" in seen
        ):
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
