"""Build playoff bracket payload from completed games (game_type heuristics)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.logo_urls import team_logo_url_for_team
from app.models import Game, Team, db


def is_playoff_game_type(game_type: str | None) -> bool:
    if not game_type:
        return False
    t = game_type.strip().lower()
    if "regular" in t or "preseason" in t or "pre-season" in t or "exhibition" in t:
        return False
    if any(
        x in t
        for x in (
            "playoff",
            "play-off",
            "postseason",
            "post-season",
            "stanley",
        )
    ):
        return True
    if t in ("po", "p", "playoffs"):
        return True
    return False


@dataclass
class SeriesAgg:
    team_a_id: int
    team_b_id: int
    wins_a: int
    wins_b: int
    games_played: int
    first_date: date | None
    last_date: date | None


def _team_json(t: Team | None) -> dict | None:
    if not t:
        return None
    return {
        "id": t.id,
        "slug": t.slug,
        "name": t.name,
        "abbreviation": t.abbreviation,
        "city": t.city or "",
        "nickname": t.nickname or "",
        "logo_url": team_logo_url_for_team(t),
    }


def _series_json(
    sa: SeriesAgg,
    teams: dict[int, Team],
) -> dict:
    ta = teams.get(sa.team_a_id)
    tb = teams.get(sa.team_b_id)
    winner_id = None
    if sa.wins_a >= 4 or sa.wins_b >= 4:
        winner_id = sa.team_a_id if sa.wins_a > sa.wins_b else sa.team_b_id
    elif sa.games_played > 0 and sa.wins_a != sa.wins_b:
        winner_id = sa.team_a_id if sa.wins_a > sa.wins_b else sa.team_b_id
    w = teams.get(winner_id) if winner_id else None
    return {
        "team_a": _team_json(ta),
        "team_b": _team_json(tb),
        "wins_a": sa.wins_a,
        "wins_b": sa.wins_b,
        "games_played": sa.games_played,
        "winner": _team_json(w),
        "series_complete": (sa.wins_a >= 4 or sa.wins_b >= 4),
        "first_game_date": sa.first_date.isoformat() if sa.first_date else None,
        "last_game_date": sa.last_date.isoformat() if sa.last_date else None,
    }


def playoff_bracket_payload(season_id: int | None) -> dict:
    """Return JSON-serializable bracket data for a season."""
    if season_id is None:
        return {
            "season_id": None,
            "empty": True,
            "message": "No season.",
            "championship": None,
            "quarterfinals": [],
            "semifinals": [],
            "rounds": [],
        }

    games = db.session.scalars(
        select(Game)
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.season_id == season_id, Game.status == "final")
    ).all()

    playoff: list[Game] = [g for g in games if is_playoff_game_type(g.game_type)]
    if not playoff:
        return {
            "season_id": season_id,
            "empty": True,
            "message": "No playoff games found. Games need a playoff-type label in the schedule import (e.g. Playoffs).",
            "championship": None,
            "quarterfinals": [],
            "semifinals": [],
            "rounds": [],
        }

    by_pair: dict[tuple[int, int], list[Game]] = {}
    for g in playoff:
        a, b = sorted([g.home_team_id, g.away_team_id])
        by_pair.setdefault((a, b), []).append(g)

    series_list: list[SeriesAgg] = []
    for (tid_a, tid_b), gl in by_pair.items():
        wa = wb = 0
        first_d: date | None = None
        last_d: date | None = None
        played = 0
        for g in gl:
            if g.home_score is None or g.away_score is None:
                continue
            played += 1
            gd = g.game_date
            if gd:
                first_d = gd if first_d is None or gd < first_d else first_d
                last_d = gd if last_d is None or gd > last_d else last_d
            if g.home_team_id == tid_a:
                if g.home_score > g.away_score:
                    wa += 1
                elif g.away_score > g.home_score:
                    wb += 1
            else:
                # home is tid_b
                if g.home_score > g.away_score:
                    wb += 1
                elif g.away_score > g.home_score:
                    wa += 1
        series_list.append(
            SeriesAgg(
                team_a_id=tid_a,
                team_b_id=tid_b,
                wins_a=wa,
                wins_b=wb,
                games_played=played,
                first_date=first_d,
                last_date=last_d,
            )
        )

    team_ids = set()
    for s in series_list:
        team_ids.add(s.team_a_id)
        team_ids.add(s.team_b_id)
    teams = {}
    if team_ids:
        for tm in db.session.scalars(select(Team).where(Team.id.in_(team_ids))):
            teams[tm.id] = tm

    # Order by first playoff game so rounds read left-to-right in schedule order.
    ordered = sorted(
        series_list,
        key=lambda s: (s.first_date or date.min, s.team_a_id, s.team_b_id),
    )
    n = len(ordered)

    def partition_rounds() -> tuple[list[SeriesAgg], list[SeriesAgg], SeriesAgg | None]:
        """First round (up to 4+), second round (2), final (1).

        Do **not** infer the final from latest game date — that mis-labels a first-round
        series as the championship when every matchup is still the same round (e.g. four
        parallel best-of-seven series).
        """
        if n == 0:
            return [], [], None
        if n == 1:
            return [], [], ordered[0]
        if n == 2:
            return [], ordered, None
        if n == 3:
            return [], ordered[:2], ordered[2]
        if n == 4:
            return ordered, [], None
        if n == 5:
            return ordered[:4], ordered[4:5], None
        if n == 6:
            return ordered[:4], ordered[4:6], None
        if n >= 7:
            return ordered[:-3], ordered[-3:-1], ordered[-1]
        raise AssertionError(f"Unexpected playoff series count: {n}")

    quarterfinals, semifinals, championship_series = partition_rounds()

    def pack_rounds_fallback(sl: list[SeriesAgg]) -> list[dict]:
        if not sl:
            return []
        n = len(sl)
        if n <= 2:
            return [{"label": "Playoff series", "series": [_series_json(x, teams) for x in sl]}]
        third = (n + 2) // 3
        chunks = [sl[:third], sl[third : 2 * third], sl[2 * third :]]
        labels = ("Round 1", "Round 2", "Semifinals")
        out = []
        for lab, chunk in zip(labels, chunks):
            if chunk:
                out.append({"label": lab, "series": [_series_json(x, teams) for x in chunk]})
        return out

    # Legacy "rounds" grid for older clients; UI should use quarterfinals / semifinals / championship.
    rounds = (
        [
            {"label": "First round", "series": [_series_json(x, teams) for x in quarterfinals]},
            {"label": "Second round", "series": [_series_json(x, teams) for x in semifinals]},
        ]
        if quarterfinals or semifinals
        else pack_rounds_fallback(ordered)
    )

    champ_j = _series_json(championship_series, teams) if championship_series else None

    return {
        "season_id": season_id,
        "empty": False,
        "message": "",
        "championship": champ_j,
        "quarterfinals": [_series_json(x, teams) for x in quarterfinals],
        "semifinals": [_series_json(x, teams) for x in semifinals],
        "rounds": rounds,
        "series_total": n,
    }
