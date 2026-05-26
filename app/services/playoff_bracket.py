"""Build playoff bracket payload for Historical, Fantasy, and Cap.

When a postseason has not started (or only partly imported), opening-round slots are
filled from current regular-season standings with series-win predictions. Scheduled and
final playoff games both count toward visible matchups; wins count only from final games.

Empty slots in the **next** playoff round only may show a projected 0–0 matchup
(``preview_only``) when **both** feeder series are **real** (from the schedule import)
and **clinched** (a team at 4 wins). Synthetic previews are **not** chained: e.g. no
conference-finals or championship projection while semifinal slots are still preview-only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from flask import current_app, has_app_context
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.logo_urls import team_logo_url_for_team
from app.models import Game, Season, Team, TeamStanding, db
from app.services.playoff_series_prediction import (
    PREDICTION_METHOD_NOTE,
    load_rs_head_to_head,
    load_rs_strength_by_team,
    matchup_prediction_dict,
)
from app.services.league_rules import get_rule_value
from app.services.season_team_logo_bundle import dashboard_team_logo_url


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


def _is_regular_season_game_type(game_type: str | None) -> bool:
    if not game_type:
        return False
    t = game_type.strip().lower()
    return "regular" in t or t in ("rs", "r")


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

_PROJECTED_PAIRINGS_8: tuple[tuple[int, int], ...] = ((0, 7), (3, 4), (2, 5), (1, 6))
_PROJECTED_PAIRINGS_4: tuple[tuple[int, int], ...] = ((0, 3), (1, 2))
_PROJECTED_PAIRINGS_4_HISTORICAL_DEFAULT: tuple[tuple[int, int], ...] = ((0, 2), (1, 3))

_PROJECTION_NOTE = (
    "Projected from current regular-season standings; matchups update as games are imported. "
    "Higher seeds are treated as having home-ice advantage."
)
_BRACKET_CACHE_VERSION = "postseason-window-v2"
_PROJECTION_RULE_KEY = "playoff_projection_first_round_format"


def _league_slug() -> str:
    if not has_app_context():
        return ""
    return str(current_app.config.get("LEAGUE_SLUG") or "").strip()


def _compact_mirror_opening_round() -> bool:
    """Historical mirror UI renders the opening round in ``second_round`` slots."""
    return _league_slug() == "bowl-historical"


def _projection_format_key() -> str:
    slug = _league_slug()
    raw = str(get_rule_value(db.session, slug, _PROJECTION_RULE_KEY, "default") or "").strip().lower()
    if raw in {"", "default"}:
        return "division_1v3_2v4" if slug == "bowl-historical" else "conference_division_winners_top3"
    aliases = {
        "division-winners-top3": "conference_division_winners_top3",
        "division_winners_top3": "conference_division_winners_top3",
        "top8": "conference_points",
        "conference_top8": "conference_points",
        "1v3": "division_1v3_2v4",
        "1v4": "division_1v4_2v3",
    }
    return aliases.get(raw, raw)


def _division_key_for_standing(st: TeamStanding) -> tuple[int, str]:
    team = getattr(st, "team", None)
    if team is not None and team.fhm_division_id is not None:
        return (int(team.fhm_division_id), "")
    return (-1, str(st.division or "").strip().lower())


def _seed_conference_with_division_winners(rows: list[TeamStanding]) -> list[TeamStanding]:
    """Seed top 8 by conference with division winners in the top slots by points."""
    by_division: dict[tuple[int, str], list[TeamStanding]] = {}
    for st in rows:
        by_division.setdefault(_division_key_for_standing(st), []).append(st)
    division_winners: list[TeamStanding] = []
    for div_rows in by_division.values():
        if not div_rows:
            continue
        division_winners.append(sorted(div_rows, key=_standing_sort_key, reverse=True)[0])
    division_winners.sort(key=_standing_sort_key, reverse=True)
    winner_ids = {int(st.team_id) for st in division_winners}
    remaining = [st for st in sorted(rows, key=_standing_sort_key, reverse=True) if int(st.team_id) not in winner_ids]
    return (division_winners[:3] + remaining)[:8]


def _merge_projected_empty_slots(
    slots: list[SeriesAgg | None],
    projected: list[SeriesAgg | None],
) -> list[SeriesAgg | None]:
    """Fill empty bracket cells from standings seeding without overwriting real series."""
    if not projected:
        return list(slots)
    out = list(slots)
    for i, proj in enumerate(projected):
        if i >= len(out):
            break
        if out[i] is None and proj is not None:
            out[i] = proj
    return out


def _current_postseason_games(games: list[Game]) -> list[Game]:
    """Use the latest postseason window, ignoring stale prior playoff games on reused season rows."""
    playoff = [g for g in games if is_playoff_game_type(g.game_type)]
    if not playoff:
        return []
    latest_regular_date = max(
        (
            g.game_date
            for g in games
            if g.game_date is not None and _is_regular_season_game_type(g.game_type)
        ),
        default=None,
    )
    if latest_regular_date is None:
        return playoff
    current = [
        g
        for g in playoff
        if g.game_date is None or g.game_date > latest_regular_date
    ]
    return current


def _playoff_window_has_started(playoff_games: list[Game]) -> bool:
    """Official bracket takes over once playoff games are today/past or have finals imported."""
    today = date.today()
    for game in playoff_games:
        if str(game.status or "").strip().lower() == "final":
            return True
        if game.game_date is not None and game.game_date <= today:
            return True
    return False


def _season_logo_year(season_id: int | None) -> int | None:
    if season_id is None:
        return None
    season = db.session.get(Season, int(season_id))
    if season is None or season.start_year is None:
        return None
    return int(season.start_year)


def _blend_opening_round_from_standings(
    season_id: int,
    s1_slots: list[SeriesAgg | None],
    s2_slots: list[SeriesAgg | None],
    s3_slots: list[SeriesAgg | None],
    teams: dict[int, Team],
) -> tuple[list[SeriesAgg | None], list[SeriesAgg | None], list[SeriesAgg | None], str]:
    """Backfill missing opening-round matchups when playoffs are starting across all leagues."""
    proj_s1, proj_s2, proj_s3, proj_teams, msg = _projected_bracket_slots_from_standings(season_id)
    if not (any(proj_s1) or any(proj_s2) or any(proj_s3)):
        return s1_slots, s2_slots, s3_slots, ""
    teams.update(proj_teams)
    if _compact_mirror_opening_round():
        s2_out = _merge_projected_empty_slots(s2_slots, proj_s2)
        if s2_out != s2_slots:
            return s1_slots, s2_out, s3_slots, msg or _PROJECTION_NOTE
        return s1_slots, s2_slots, s3_slots, ""
    s1_out = _merge_projected_empty_slots(s1_slots, proj_s1)
    if s1_out != s1_slots:
        return s1_out, s2_slots, s3_slots, msg or _PROJECTION_NOTE
    return s1_slots, s2_slots, s3_slots, ""


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


def _standing_sort_key(st: TeamStanding) -> tuple[int, int, int, int, int, str]:
    """Sort standings rows the same way the projection model breaks ties."""
    row = max(int(st.w or 0) - int(st.shootout_wins or 0), 0)
    gd = int(st.gf or 0) - int(st.ga or 0)
    name = ""
    if getattr(st, "team", None) is not None:
        name = (st.team.full_display_name() or "").lower()
    return (
        int(st.pts or 0),
        row,
        gd,
        int(st.gf or 0),
        -int(st.team_id or 0),
        name,
    )


def _projected_series_for_seeded_rows(
    rows: list[TeamStanding],
    pairings: tuple[tuple[int, int], ...],
) -> list[SeriesAgg]:
    out: list[SeriesAgg] = []
    for ai, bi in pairings:
        if ai >= len(rows) or bi >= len(rows):
            continue
        out.append(_synthetic_preview_series(int(rows[ai].team_id), int(rows[bi].team_id)))
    return out


def _projected_bracket_slots_from_standings(
    season_id: int,
) -> tuple[list[SeriesAgg | None], list[SeriesAgg | None], list[SeriesAgg | None], dict[int, Team], str]:
    """Build a current-if-season-ended bracket from regular-season standings."""
    rows = db.session.scalars(
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season_id)
    ).all()
    rows = [st for st in rows if st.team is not None]
    if not rows:
        return [], [], [], {}, "No standings rows available for projected playoff seeding."

    teams = {int(st.team_id): st.team for st in rows if st.team is not None}
    by_conf: dict[int, list[TeamStanding]] = {}
    for st in rows:
        conf = st.team.fhm_conference_id if st.team is not None else None
        if conf is None:
            continue
        by_conf.setdefault(int(conf), []).append(st)
    for conf_rows in by_conf.values():
        conf_rows.sort(key=_standing_sort_key, reverse=True)

    format_key = _projection_format_key()

    # Historical compact mirror: Division Semi-Finals -> Division Finals -> Final.
    if _compact_mirror_opening_round():
        s2_slots: list[SeriesAgg | None] = [None] * 4
        by_division: dict[tuple[int, str], list[TeamStanding]] = {}
        for st in rows:
            by_division.setdefault(_division_key_for_standing(st), []).append(st)
        division_keys = sorted(by_division.keys(), key=lambda k: (k[0], k[1]))
        pairings = (
            _PROJECTED_PAIRINGS_4
            if format_key == "division_1v4_2v3"
            else _PROJECTED_PAIRINGS_4_HISTORICAL_DEFAULT
        )
        for div_idx, div_key in enumerate(division_keys[:2]):
            seeded = sorted(by_division.get(div_key, []), key=_standing_sort_key, reverse=True)[:4]
            series = _projected_series_for_seeded_rows(seeded, pairings)
            offset = div_idx * 2
            for idx, s in enumerate(series[:2]):
                s2_slots[offset + idx] = s
        if any(s2_slots):
            return [None] * 8, s2_slots, [None] * 2, teams, _PROJECTION_NOTE

    if len(by_conf) >= 2:
        s1_slots: list[SeriesAgg | None] = [None] * 8
        for conf_id, offset in ((_CAMPBELL_CONF_ID, 0), (_WALES_CONF_ID, 4)):
            conf_rows = by_conf.get(conf_id, [])
            if format_key == "conference_points":
                seeded = sorted(conf_rows, key=_standing_sort_key, reverse=True)[:8]
            else:
                seeded = _seed_conference_with_division_winners(conf_rows)
            series = _projected_series_for_seeded_rows(seeded, _PROJECTED_PAIRINGS_8)
            for idx, s in enumerate(series[:4]):
                s1_slots[offset + idx] = s
        if any(s1_slots):
            return s1_slots, [None] * 4, [None] * 2, teams, _PROJECTION_NOTE

    # Fallback for a single-table league: top 16 overall, traditional outside-in bracket.
    overall = sorted(rows, key=_standing_sort_key, reverse=True)[:16]
    series = _projected_series_for_seeded_rows(
        overall,
        ((0, 15), (7, 8), (4, 11), (3, 12), (2, 13), (5, 10), (6, 9), (1, 14)),
    )
    s1_slots = [None] * 8
    for idx, s in enumerate(series[:8]):
        s1_slots[idx] = s
    if any(s1_slots):
        return s1_slots, [None] * 4, [None] * 2, teams, _PROJECTION_NOTE
    return [], [], [], teams, "Not enough standings rows to project a playoff bracket yet."


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
    """Mirror-side id for a series: conference when present, else division fallback."""
    ta = teams.get(s.team_a_id)
    tb = teams.get(s.team_b_id)
    ca = int(ta.fhm_conference_id) if ta and ta.fhm_conference_id is not None else None
    cb = int(tb.fhm_conference_id) if tb and tb.fhm_conference_id is not None else None
    if ca is not None and ca < 0:
        ca = None
    if cb is not None and cb < 0:
        cb = None
    if ca is not None and cb is not None and ca == cb:
        return ca
    if ca is not None:
        return ca
    if cb is not None:
        return cb
    da = int(ta.fhm_division_id) if ta and ta.fhm_division_id is not None else None
    dbv = int(tb.fhm_division_id) if tb and tb.fhm_division_id is not None else None
    if da is not None and da < 0:
        da = None
    if dbv is not None and dbv < 0:
        dbv = None
    if da is not None and dbv is not None and da == dbv:
        return da
    if da is not None:
        return da
    if dbv is not None:
        return dbv
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


def _team_json(t: Team | None, *, logo_year: int | None = None) -> dict | None:
    if not t:
        return None
    logo_url = dashboard_team_logo_url(t, logo_year) if logo_year is not None else team_logo_url_for_team(t)
    return {
        "id": t.id,
        "slug": t.slug,
        "name": t.name,
        "abbreviation": t.abbreviation,
        "city": t.city or "",
        "nickname": t.nickname or "",
        "logo_url": logo_url,
    }


def _series_json(
    sa: SeriesAgg,
    teams: dict[int, Team],
    *,
    rs_map: dict[int, dict[str, float]] | None = None,
    h2h: dict[tuple[int, int], tuple[int, int, int]] | None = None,
    logo_year: int | None = None,
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
        "team_a": _team_json(ta, logo_year=logo_year),
        "team_b": _team_json(tb, logo_year=logo_year),
        "wins_a": sa.wins_a,
        "wins_b": sa.wins_b,
        "games_played": sa.games_played,
        "winner": _team_json(w, logo_year=logo_year),
        "series_complete": (sa.wins_a >= 4 or sa.wins_b >= 4),
        "first_game_date": sa.first_date.isoformat() if sa.first_date else None,
        "last_game_date": sa.last_date.isoformat() if sa.last_date else None,
        "prediction": pred,
        "preview_only": bool(getattr(sa, "preview_only", False)),
    }


def playoff_bracket_cache_fingerprint(season_id: int | None) -> str:
    """Small cache key fragment that changes when playoff schedule/results change."""
    if season_id is None:
        return "no-season"
    rows = db.session.scalars(
        select(Game).where(Game.season_id == int(season_id))
    ).all()
    current_playoff = _current_postseason_games(list(rows))
    if current_playoff and not _playoff_window_has_started(current_playoff):
        current_playoff = []
    parts: list[str] = []
    for game in current_playoff:
        parts.append(
            "|".join(
                (
                    str(game.id),
                    game.game_date.isoformat() if game.game_date else "",
                    str(game.home_team_id),
                    str(game.away_team_id),
                    "" if game.home_score is None else str(game.home_score),
                    "" if game.away_score is None else str(game.away_score),
                    str(game.status or ""),
                    str(game.game_type or ""),
                )
            )
        )
    if not parts:
        standings = db.session.scalars(
            select(TeamStanding)
            .options(joinedload(TeamStanding.team))
            .where(TeamStanding.season_id == int(season_id))
        ).all()
        st_parts: list[str] = [_projection_format_key()]
        for st in standings:
            team = getattr(st, "team", None)
            st_parts.append(
                "|".join(
                    (
                        str(st.team_id),
                        str(st.pts or 0),
                        str(st.w or 0),
                        str(st.l or 0),
                        str(st.ties or 0),
                        str(st.otl or 0),
                        str(st.gf or 0),
                        str(st.ga or 0),
                        str(team.fhm_conference_id if team is not None else ""),
                        str(team.fhm_division_id if team is not None else ""),
                        str(st.division or ""),
                    )
                )
            )
        digest = hashlib.sha1("\n".join(sorted(st_parts)).encode("utf-8")).hexdigest()[:16]
        return f"{_BRACKET_CACHE_VERSION}-projection-{len(standings)}-{digest}"
    digest = hashlib.sha1("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]
    return f"{_BRACKET_CACHE_VERSION}-{len(parts)}-{digest}"


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
        select(
            Game
        )
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.season_id == season_id)
    ).all()

    playoff: list[Game] = _current_postseason_games(list(games))
    if playoff and not _playoff_window_has_started(playoff):
        playoff = []
    if not playoff:
        rs_map = load_rs_strength_by_team(db.session, season_id)
        h2h = load_rs_head_to_head(db.session, season_id)
        logo_year = _season_logo_year(season_id)
        s1_slots, s2_slots, s3_slots, teams, projection_message = (
            _projected_bracket_slots_from_standings(season_id)
        )
        if any(s1_slots) or any(s2_slots) or any(s3_slots):
            def _slot_json(s: SeriesAgg | None) -> dict | None:
                return _series_json(s, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) if s else None

            first_round = [_slot_json(s) for s in s1_slots]
            second_round = [_slot_json(s) for s in s2_slots]
            conference_finals = [_slot_json(s) for s in s3_slots]
            rounds = []
            if any(first_round):
                rounds.append(
                    {
                        "label": "Projected first round",
                        "series": [s for s in first_round if s is not None],
                    }
                )
            if any(second_round):
                rounds.append(
                    {
                        "label": "Projected division semifinals",
                        "series": [s for s in second_round if s is not None],
                    }
                )
            return {
                "season_id": season_id,
                "empty": False,
                "message": projection_message,
                "projection_only": True,
                "prediction_method_note": PREDICTION_METHOD_NOTE,
                "championship": None,
                "first_round": first_round,
                "second_round": second_round,
                "conference_finals": conference_finals,
                "quarterfinals": [s for s in first_round if s is not None],
                "semifinals": [s for s in second_round if s is not None],
                "rounds": rounds,
                "series_total": len([s for s in first_round + second_round + conference_finals if s is not None]),
            }
        return {
            "season_id": season_id,
            "empty": True,
            "message": projection_message,
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
    for (_pair_a, _pair_b), gl in by_pair.items():
        gl = sorted(gl, key=lambda g: (g.game_date or date.min, g.id))
        first_game = gl[0]
        tid_a = int(first_game.away_team_id)
        tid_b = int(first_game.home_team_id)
        wa = wb = 0
        first_d: date | None = None
        last_d: date | None = None
        played = 0
        for g in gl:
            gd = g.game_date
            if gd:
                first_d = gd if first_d is None or gd < first_d else first_d
                last_d = gd if last_d is None or gd > last_d else last_d
            is_final = str(g.status or "").strip().lower() == "final"
            if not is_final or g.home_score is None or g.away_score is None:
                continue
            played += 1
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
    logo_year = _season_logo_year(season_id)

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
        Historical's compact mirror uses second_round as the visible opening round.
        For other smaller brackets, preserve the previous 4+2(+1) semantics and map in the UI.
        """
        if n == 0:
            return [], [], [], None
        if _compact_mirror_opening_round():
            if n == 1:
                return [], [ordered[0]], [], None
            if n <= 4:
                return [], ordered, [], None
            if n <= 6:
                return [], ordered[:4], ordered[4:n], None
            return [], ordered[:4], ordered[4:6], ordered[6]
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
    s1_slots, s2_slots, s3_slots, blend_message = _blend_opening_round_from_standings(
        season_id, s1_slots, s2_slots, s3_slots, teams
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
            return [{"label": "Playoff series", "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in sl]}]
        third = (n + 2) // 3
        chunks = [sl[:third], sl[third : 2 * third], sl[2 * third :]]
        labels = ("Round 1", "Round 2", "Semifinals")
        out = []
        for lab, chunk in zip(labels, chunks):
            if chunk:
                out.append({"label": lab, "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in chunk]})
        return out

    def _slot_json(s: SeriesAgg | None) -> dict | None:
        return _series_json(s, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) if s else None

    # Legacy "rounds" grid for older clients.
    rounds = (
        [
            {
                "label": "First round",
                "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in quarterfinals],
            },
            {
                "label": "Second round",
                "series": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in semifinals],
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
                    _series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year)
                    for x in r3_ordered
                    if x is not None
                ],
            }
        )

    champ_j = (
        _series_json(championship_series, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year)
        if championship_series
        else None
    )

    return {
        "season_id": season_id,
        "empty": False,
        "message": blend_message,
        "prediction_method_note": PREDICTION_METHOD_NOTE,
        "championship": champ_j,
        "first_round": [_slot_json(s) for s in s1_slots],
        "second_round": [_slot_json(s) for s in s2_slots],
        "conference_finals": [_slot_json(s) for s in s3_slots],
        "quarterfinals": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in quarterfinals],
        "semifinals": [_series_json(x, teams, rs_map=rs_map, h2h=h2h, logo_year=logo_year) for x in semifinals],
        "rounds": rounds,
        "series_total": n,
    }
