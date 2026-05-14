"""Build playoff bracket payload from completed games (game_type heuristics).

Empty slots in the **next** playoff round only may show a projected 0–0 matchup
(``preview_only``) when **both** feeder series are **real** (from the schedule import)
and **clinched** (a team at 4 wins). Synthetic previews are **not** chained: e.g. no
conference-finals or championship projection while semifinal slots are still preview-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.logo_urls import team_logo_url_for_team
from app.models import Game, Team, db
from app.services.playoff_series_prediction import (
    PREDICTION_METHOD_NOTE,
    load_rs_head_to_head,
    load_rs_strength_by_team,
    matchup_prediction_dict,
)


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
    #: True when this row is inferred for empty bracket slots (no games yet in this pairing).
    preview_only: bool = False


# FHM conferences.csv across league imports: 0 = Wales (East), 1 = Campbell (West).
_WALES_CONF_ID = 0
_CAMPBELL_CONF_ID = 1


def _series_sort_key(s: SeriesAgg) -> tuple:
    return (s.first_date or date.min, s.team_a_id, s.team_b_id)


def _preview_winner_team_id(s: SeriesAgg, rs_map: dict[int, dict[str, float]]) -> int | None:
    """Who advances this slot for bracket preview: clinch, leader, or RS points-rate tiebreaker."""
    if s.wins_a >= 4:
        return int(s.team_a_id)
    if s.wins_b >= 4:
        return int(s.team_b_id)
    if s.games_played > 0 and s.wins_a != s.wins_b:
        return int(s.team_a_id) if s.wins_a > s.wins_b else int(s.team_b_id)
    ra = float(rs_map.get(int(s.team_a_id), {}).get("pts_rate", 0) or 0)
    rb = float(rs_map.get(int(s.team_b_id), {}).get("pts_rate", 0) or 0)
    if ra > rb:
        return int(s.team_a_id)
    if rb > ra:
        return int(s.team_b_id)
    return int(s.team_a_id)


def _synthetic_preview_series(team_a_id: int, team_b_id: int) -> SeriesAgg:
    return SeriesAgg(
        team_a_id=int(team_a_id),
        team_b_id=int(team_b_id),
        wins_a=0,
        wins_b=0,
        games_played=0,
        first_date=None,
        last_date=None,
        preview_only=True,
    )


def _series_is_clinched(s: SeriesAgg) -> bool:
    return int(s.wins_a) >= 4 or int(s.wins_b) >= 4


def _is_real_series_slot(s: SeriesAgg | None) -> bool:
    """True for series aggregated from played games (not heuristic bracket filler)."""
    return s is not None and not bool(getattr(s, "preview_only", False))


def _all_non_null_slots_real_and_clinched(slots: list[SeriesAgg | None]) -> bool:
    """True when every populated slot in this round is a real series that has finished (4 wins)."""
    for s in slots:
        if s is None:
            continue
        if not _is_real_series_slot(s) or not _series_is_clinched(s):
            return False
    return True


def _fill_mirror_slots_with_preview(
    s1: list[SeriesAgg | None],
    s2: list[SeriesAgg | None],
    s3: list[SeriesAgg | None],
    championship_series: SeriesAgg | None,
    rs_map: dict[int, dict[str, float]],
) -> tuple[list[SeriesAgg | None], list[SeriesAgg | None], list[SeriesAgg | None], SeriesAgg | None]:
    """Fill empty slots only one bracket level ahead of completed **real** series (no chaining)."""
    s2_out = list(s2)
    s3_out = list(s3)
    champ_out = championship_series

    if _all_non_null_slots_real_and_clinched(s1):
        for i in range(4):
            if s2_out[i] is not None:
                continue
            a = s1[2 * i] if 2 * i < 8 else None
            b = s1[2 * i + 1] if 2 * i + 1 < 8 else None
            if not _is_real_series_slot(a) or not _is_real_series_slot(b):
                continue
            if not _series_is_clinched(a) or not _series_is_clinched(b):
                continue
            wa = _preview_winner_team_id(a, rs_map)
            wb = _preview_winner_team_id(b, rs_map)
            if wa is None or wb is None:
                continue
            s2_out[i] = _synthetic_preview_series(wa, wb)

    if _all_non_null_slots_real_and_clinched(s2_out):
        for i in range(2):
            if s3_out[i] is not None:
                continue
            pa = s2_out[2 * i] if 2 * i < 4 else None
            pb = s2_out[2 * i + 1] if 2 * i + 1 < 4 else None
            if not _is_real_series_slot(pa) or not _is_real_series_slot(pb):
                continue
            if not _series_is_clinched(pa) or not _series_is_clinched(pb):
                continue
            wpa = _preview_winner_team_id(pa, rs_map)
            wpb = _preview_winner_team_id(pb, rs_map)
            if wpa is None or wpb is None:
                continue
            s3_out[i] = _synthetic_preview_series(wpa, wpb)

    if (
        champ_out is None
        and s3_out[0] is not None
        and s3_out[1] is not None
        and _all_non_null_slots_real_and_clinched(s3_out)
    ):
        if not _is_real_series_slot(s3_out[0]) or not _is_real_series_slot(s3_out[1]):
            return s2_out, s3_out, champ_out
        if not _series_is_clinched(s3_out[0]) or not _series_is_clinched(s3_out[1]):
            return s2_out, s3_out, champ_out
        ca = _preview_winner_team_id(s3_out[0], rs_map)
        cb = _preview_winner_team_id(s3_out[1], rs_map)
        if ca is not None and cb is not None:
            champ_out = _synthetic_preview_series(ca, cb)

    return s2_out, s3_out, champ_out


def _series_conference_id(s: SeriesAgg, teams: dict[int, Team]) -> int | None:
    """FHM conference id for a series when both teams agree, else first known side."""
    ta = teams.get(s.team_a_id)
    tb = teams.get(s.team_b_id)
    ca = int(ta.fhm_conference_id) if ta and ta.fhm_conference_id is not None else None
    cb = int(tb.fhm_conference_id) if tb and tb.fhm_conference_id is not None else None
    if ca is not None and cb is not None and ca == cb:
        return ca
    if ca is not None:
        return ca
    if cb is not None:
        return cb
    return None


def _reorder_mirror_qf_series(first8: list[SeriesAgg], teams: dict[int, Team]) -> list[SeriesAgg]:
    """Mirror bracket: left column Campbell (West), right column Wales (East)."""
    campbell = sorted(
        [s for s in first8 if _series_conference_id(s, teams) == _CAMPBELL_CONF_ID],
        key=_series_sort_key,
    )
    wales = sorted(
        [s for s in first8 if _series_conference_id(s, teams) == _WALES_CONF_ID],
        key=_series_sort_key,
    )
    left = campbell[:4]
    right = wales[:4]
    pool = sorted([s for s in first8 if s not in left and s not in right], key=_series_sort_key)
    for s in pool:
        if len(left) < 4:
            left.append(s)
        elif len(right) < 4:
            right.append(s)
    return left + right


def _reorder_mirror_round2_for_slots(
    r2: list[SeriesAgg], teams: dict[int, Team]
) -> list[SeriesAgg | None]:
    """Semifinals: indices 0–1 = Campbell (West), 2–3 = Wales (East); matches mirror UI."""
    if not r2:
        return []

    if len(r2) == 1:
        s = r2[0]
        side = _series_conference_id(s, teams)
        if side == _WALES_CONF_ID:
            return [None, None, s, None]
        return [s, None, None, None]

    camp = sorted(
        [s for s in r2 if _series_conference_id(s, teams) == _CAMPBELL_CONF_ID],
        key=_series_sort_key,
    )
    wales = sorted(
        [s for s in r2 if _series_conference_id(s, teams) == _WALES_CONF_ID],
        key=_series_sort_key,
    )
    pool = sorted(
        [s for s in r2 if _series_conference_id(s, teams) is None],
        key=_series_sort_key,
    )

    if not camp and not wales and pool:
        pl = list(pool)
        if len(pl) == 2:
            return [pl[0], None, pl[1], None]
        if len(pl) == 3:
            return [pl[0], pl[1], pl[2], None]
        if len(pl) >= 4:
            return [pl[0], pl[1], pl[2], pl[3]]
        return [pl[0], None, None, None]

    left: list[SeriesAgg] = list(camp[:2])
    right: list[SeriesAgg] = list(wales[:2])
    for s in pool:
        if len(left) < 2:
            left.append(s)
        elif len(right) < 2:
            right.append(s)
        else:
            left.append(s)

    out: list[SeriesAgg | None] = [
        left[0] if len(left) > 0 else None,
        left[1] if len(left) > 1 else None,
        right[0] if len(right) > 0 else None,
        right[1] if len(right) > 1 else None,
    ]
    return out


def _reorder_mirror_round3_for_slots(
    r3: list[SeriesAgg], teams: dict[int, Team]
) -> list[SeriesAgg | None]:
    """Conference finals: index 0 = Campbell (West), 1 = Wales (East)."""
    if not r3:
        return []
    if len(r3) == 1:
        s = r3[0]
        side = _series_conference_id(s, teams)
        if side == _WALES_CONF_ID:
            return [None, s]
        return [s, None]

    camp = sorted(
        [s for s in r3 if _series_conference_id(s, teams) == _CAMPBELL_CONF_ID],
        key=_series_sort_key,
    )
    wales = sorted(
        [s for s in r3 if _series_conference_id(s, teams) == _WALES_CONF_ID],
        key=_series_sort_key,
    )
    unk = sorted(
        [s for s in r3 if _series_conference_id(s, teams) is None],
        key=_series_sort_key,
    )
    if not camp and not wales and unk:
        u = unk[:2]
        return [u[0], u[1] if len(u) > 1 else None]

    left = list(camp[:1])
    right = list(wales[:1])
    for u in unk:
        if not left:
            left.append(u)
        elif not right:
            right.append(u)
        else:
            left.append(u)
    return [
        left[0] if left else None,
        right[0] if right else None,
    ]


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
    *,
    rs_map: dict[int, dict[str, float]] | None = None,
    h2h: dict[tuple[int, int], tuple[int, int, int]] | None = None,
) -> dict:
    ta = teams.get(sa.team_a_id)
    tb = teams.get(sa.team_b_id)
    winner_id = None
    if sa.wins_a >= 4 or sa.wins_b >= 4:
        winner_id = sa.team_a_id if sa.wins_a > sa.wins_b else sa.team_b_id
    elif sa.games_played > 0 and sa.wins_a != sa.wins_b:
        winner_id = sa.team_a_id if sa.wins_a > sa.wins_b else sa.team_b_id
    w = teams.get(winner_id) if winner_id else None
    pred = None
    if rs_map is not None and h2h is not None:
        pred = matchup_prediction_dict(
            team_a_id=sa.team_a_id,
            team_b_id=sa.team_b_id,
            wins_a=sa.wins_a,
            wins_b=sa.wins_b,
            rs_map=rs_map,
            h2h=h2h,
            teams=teams,
        )
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
        "prediction": pred,
        "preview_only": bool(getattr(sa, "preview_only", False)),
    }


def playoff_bracket_payload(season_id: int | None) -> dict:
    """Return JSON-serializable bracket data for a season."""
    if season_id is None:
        return {
            "season_id": None,
            "empty": True,
            "message": "No season.",
            "championship": None,
            "first_round": [],
            "second_round": [],
            "conference_finals": [],
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
            "first_round": [],
            "second_round": [],
            "conference_finals": [],
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

    rs_map = load_rs_strength_by_team(db.session, season_id)
    h2h = load_rs_head_to_head(db.session, season_id)

    # Order by first playoff game so rounds read left-to-right in schedule order.
    ordered = sorted(
        series_list,
        key=lambda s: (s.first_date or date.min, s.team_a_id, s.team_b_id),
    )
    n = len(ordered)

    if n >= 8:
        ordered = list(ordered)
        ordered[:8] = _reorder_mirror_qf_series(ordered[:8], teams)

    def semantic_playoff_rounds() -> tuple[list[SeriesAgg], list[SeriesAgg], list[SeriesAgg], SeriesAgg | None]:
        """Split ordered series into outer→inner rounds (by schedule order).

        For 8+ series, assume bracket order: 8 first-round, then 4, then 2, then championship.
        For smaller brackets, preserve the previous 4+2(+1) semantics and map in the UI.
        """
        if n == 0:
            return [], [], [], None
        if n == 1:
            return [], [], [], ordered[0]
        if n == 2:
            return [], ordered, [], None
        if n == 3:
            return [], ordered[:2], [], ordered[2]
        if n == 4:
            return ordered, [], [], None
        if n == 5:
            return ordered[:4], ordered[4:], [], None
        if n == 6:
            return ordered[:4], ordered[4:6], [], None
        if n == 7:
            return ordered[:4], ordered[4:6], [], ordered[6]
        # n >= 8: up to 8–4–2–1 series in order.
        r1 = list(ordered[:8])
        r2 = list(ordered[8 : min(n, 12)])
        r3 = list(ordered[12 : min(n, 14)])
        champ = ordered[14] if n >= 15 else None
        return r1, r2, r3, champ

    def expand_to_mirror_slots(
        r1: list[SeriesAgg],
        r2: list[SeriesAgg | None],
        r3: list[SeriesAgg | None],
        champ: SeriesAgg | None,
    ) -> tuple[list[SeriesAgg | None], list[SeriesAgg | None], list[SeriesAgg | None], SeriesAgg | None]:
        """Fixed slots for mirror UI: 8 QF (4+4), 4 SF (2+2), 2 conference finals (1+1)."""
        s1: list[SeriesAgg | None] = [None] * 8
        for i, s in enumerate(r1[:8]):
            s1[i] = s
        s2: list[SeriesAgg | None] = [None] * 4
        lr2 = len(r2)
        if lr2 == 1:
            s2[0] = r2[0]
        elif lr2 == 2:
            s2[0], s2[2] = r2[0], r2[1]
        elif lr2 == 3:
            s2[0], s2[1], s2[2] = r2[0], r2[1], r2[2]
        elif lr2 >= 4:
            for i in range(4):
                s2[i] = r2[i] if i < lr2 else None
        s3: list[SeriesAgg | None] = [None] * 2
        lr3 = len(r3)
        if lr3 == 1:
            s3[0] = r3[0]
        elif lr3 >= 2:
            s3[0] = r3[0] if lr3 > 0 else None
            s3[1] = r3[1] if lr3 > 1 else None
        return s1, s2, s3, champ

    r1_sem, r2_sem, r3_sem, championship_series = semantic_playoff_rounds()
    r2_ordered: list[SeriesAgg | None] = (
        _reorder_mirror_round2_for_slots(list(r2_sem), teams) if r2_sem else []
    )
    r3_ordered: list[SeriesAgg | None] = (
        _reorder_mirror_round3_for_slots(list(r3_sem), teams) if r3_sem else []
    )
    s1_slots, s2_slots, s3_slots, championship_series = expand_to_mirror_slots(
        r1_sem, r2_ordered, r3_ordered, championship_series
    )
    s2_slots, s3_slots, championship_series = _fill_mirror_slots_with_preview(
        s1_slots, s2_slots, s3_slots, championship_series, rs_map
    )

    # Legacy field names: non-null series for older consumers (mirror: West then East slots).
    quarterfinals = [s for s in r1_sem if s is not None]
    semifinals = [s for s in r2_ordered if s is not None]

    def pack_rounds_fallback(sl: list[SeriesAgg]) -> list[dict]:
        if not sl:
            return []
        n = len(sl)
        if n <= 2:
            return [{"label": "Playoff series", "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in sl]}]
        third = (n + 2) // 3
        chunks = [sl[:third], sl[third : 2 * third], sl[2 * third :]]
        labels = ("Round 1", "Round 2", "Semifinals")
        out = []
        for lab, chunk in zip(labels, chunks):
            if chunk:
                out.append({"label": lab, "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in chunk]})
        return out

    def _slot_json(s: SeriesAgg | None) -> dict | None:
        return _series_json(s, teams, rs_map=rs_map, h2h=h2h) if s else None

    # Legacy "rounds" grid for older clients.
    rounds = (
        [
            {
                "label": "First round",
                "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in quarterfinals],
            },
            {
                "label": "Second round",
                "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in semifinals],
            },
        ]
        if quarterfinals or semifinals
        else pack_rounds_fallback(ordered)
    )
    if r3_ordered:
        rounds.append(
            {
                "label": "Conference finals",
                "series": [
                    _series_json(x, teams, rs_map=rs_map, h2h=h2h)
                    for x in r3_ordered
                    if x is not None
                ],
            }
        )

    champ_j = (
        _series_json(championship_series, teams, rs_map=rs_map, h2h=h2h)
        if championship_series
        else None
    )

    return {
        "season_id": season_id,
        "empty": False,
        "message": "",
        "prediction_method_note": PREDICTION_METHOD_NOTE,
        "championship": champ_j,
        "first_round": [_slot_json(s) for s in s1_slots],
        "second_round": [_slot_json(s) for s in s2_slots],
        "conference_finals": [_slot_json(s) for s in s3_slots],
        "quarterfinals": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in quarterfinals],
        "semifinals": [_series_json(x, teams, rs_map=rs_map, h2h=h2h) for x in semifinals],
        "rounds": rounds,
        "series_total": n,
    }
