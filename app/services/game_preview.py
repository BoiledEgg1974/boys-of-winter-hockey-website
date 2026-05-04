"""Pre-game matchup preview: odds, trends, recent H2H, projected starters (FHM import data)."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from flask import current_app, url_for
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.logo_urls import team_logo_url_for_team
from app.models import Game, GameSkaterStat, Player, PlayerGoalieStat, Team, TeamSeasonAggregate, TeamStanding, db
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.playoff_series_prediction import (
    _is_regular_season_game,
    load_rs_head_to_head,
    load_rs_strength_by_team,
    _h2h_per_game_margin_for_team_a,
    _logistic_stable,
    _team_rating,
)

PREVIEW_METHOD_NOTE = (
    "Single-game estimate from regular-season points pace and goal differential, "
    "home-ice adjustment, and season head-to-head goal margin in non-playoff games. "
    "Not betting advice."
)


def _player_photo_url(pl: Player | None) -> str:
    if not pl:
        return ""
    static_root = Path(current_app.root_path) / "static"
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    if not rel:
        return ""
    return url_for("static", filename=rel)


def _fmt_toi(sec: int | None) -> str | None:
    if sec is None or sec <= 0:
        return None
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _team_standing_row(session, season_id: int, team_id: int) -> TeamStanding | None:
    return session.scalars(
        select(TeamStanding).where(
            TeamStanding.season_id == season_id,
            TeamStanding.team_id == team_id,
        )
    ).first()


def _conference_key(st: TeamStanding) -> str:
    name = (st.conference or "").strip()
    if name:
        return name.lower()
    tm = st.team
    if tm is not None and tm.fhm_conference_id is not None:
        return f"id:{int(tm.fhm_conference_id)}"
    return ""


def _standing_blurb(session, season_id: int, team_id: int) -> str | None:
    rows = session.scalars(
        select(TeamStanding).options(joinedload(TeamStanding.team)).where(TeamStanding.season_id == season_id)
    ).all()
    if not rows:
        return None
    by_conf: dict[str, list[TeamStanding]] = defaultdict(list)
    for r in rows:
        k = _conference_key(r)
        if not k:
            continue
        by_conf[k].append(r)
    mine = _team_standing_row(session, season_id, team_id)
    if not mine:
        return None
    ck = _conference_key(mine)
    if not ck or ck not in by_conf:
        league_sorted = sorted(
            rows,
            key=lambda x: (-int(x.pts or 0), -(int(x.gf or 0) - int(x.ga or 0))),
        )
        for i, r in enumerate(league_sorted, start=1):
            if int(r.team_id) == team_id:
                return f"{_ordinal(i)} in league"
        return None
    conf_rows = by_conf[ck]
    conf_rows.sort(key=lambda x: (-int(x.pts or 0), -(int(x.gf or 0) - int(x.ga or 0))))
    display = (mine.conference or "").strip()
    if not display:
        if mine.team and mine.team.fhm_conference_id is not None:
            display = "Conference"
        else:
            display = "League"
    for i, r in enumerate(conf_rows, start=1):
        if int(r.team_id) == team_id:
            return f"{_ordinal(i)} in {display}"
    return None


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _pp_pk_ranks_for_rs(session, season_id: int) -> tuple[dict[int, int], dict[int, int]]:
    aggs = session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season_id,
            TeamSeasonAggregate.stat_segment == "rs",
        )
    ).all()
    pp_vals: list[tuple[int, float]] = []
    pk_vals: list[tuple[int, float]] = []
    for a in aggs:
        tid = int(a.team_id)
        if a.pp_chances and a.pp_chances > 0 and a.pp_goals is not None:
            pp_vals.append((tid, float(a.pp_goals) / float(a.pp_chances)))
        if a.sh_chances and a.sh_chances > 0 and a.pk_goals_against is not None:
            pk_vals.append(
                (tid, 100.0 - (100.0 * float(a.pk_goals_against) / float(a.sh_chances)))
            )
    pp_vals.sort(key=lambda x: x[1], reverse=True)
    pk_vals.sort(key=lambda x: x[1], reverse=True)

    def assign_ranks(vals: list[tuple[int, float]]) -> dict[int, int]:
        out: dict[int, int] = {}
        prev = None
        rank = 0
        for idx, (tid, v) in enumerate(vals, start=1):
            if prev is None or abs(v - prev) > 1e-12:
                rank = idx
                prev = v
            out[tid] = rank
        return out

    return assign_ranks(pp_vals), assign_ranks(pk_vals)


def _games_before(session, season_id: int, team_id: int, anchor: Game) -> list[Game]:
    """Most recent final games for team in season strictly before anchor (by date, then id)."""
    q = (
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            (Game.home_team_id == team_id) | (Game.away_team_id == team_id),
            Game.id != anchor.id,
        )
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
    )
    candidates = list(session.scalars(q).all())
    out: list[Game] = []
    ad = anchor.game_date
    aid = anchor.id
    for g in candidates:
        if ad is not None and g.game_date is not None:
            if g.game_date < ad:
                out.append(g)
            elif g.game_date == ad and g.id < aid:
                out.append(g)
        elif g.id < aid:
            out.append(g)
        if len(out) >= 12:
            break
    return out[:10]


def _wl_otl_for_team(g: Game, team_id: int) -> str:
    hs = g.home_score
    aws = g.away_score
    if hs is None or aws is None:
        return "?"
    if int(g.home_team_id) == team_id:
        tf, ta = int(hs), int(aws)
    else:
        tf, ta = int(aws), int(hs)
    if tf > ta:
        return "W"
    if tf < ta:
        if g.went_to_overtime or g.went_to_shootout:
            return "OTL"
        return "L"
    return "T"


def _last_10_record(games: list[Game], team_id: int) -> dict[str, Any]:
    w = l = otl = t = 0
    for g in games:
        r = _wl_otl_for_team(g, team_id)
        if r == "W":
            w += 1
        elif r == "L":
            l += 1
        elif r == "OTL":
            otl += 1
        elif r == "T":
            t += 1
    return {
        "w": w,
        "l": l,
        "otl": otl,
        "ties": t,
        "str": f"{w}-{l}-{otl}" + (f"-{t}" if t else ""),
    }


def _season_h2h_vs_opponent(
    session,
    season_id: int,
    team_id: int,
    opp: Team,
    exclude_game_id: int,
) -> dict[str, Any]:
    """W-L-OTL for team_id vs opp this season: final regular-season games only (excludes exclude_game_id)."""
    oid = int(opp.id)
    abbr = (opp.abbreviation or "").strip()
    q = (
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.id != exclude_game_id,
            (
                ((Game.home_team_id == team_id) & (Game.away_team_id == oid))
                | ((Game.home_team_id == oid) & (Game.away_team_id == team_id))
            ),
        )
        .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
    )
    games = [g for g in session.scalars(q).all() if _is_regular_season_game(g.game_type)]
    if not games:
        return {
            "gp": 0,
            "w": 0,
            "l": 0,
            "otl": 0,
            "ties": 0,
            "str": None,
            "opponent_abbr": abbr,
        }
    w = l = otl = t = 0
    for g in games:
        r = _wl_otl_for_team(g, team_id)
        if r == "W":
            w += 1
        elif r == "L":
            l += 1
        elif r == "OTL":
            otl += 1
        elif r == "T":
            t += 1
    return {
        "gp": len(games),
        "w": w,
        "l": l,
        "otl": otl,
        "ties": t,
        "str": f"{w}-{l}-{otl}" + (f"-{t}" if t else ""),
        "opponent_abbr": abbr,
    }


def _hot_cold_skaters(session, game_ids: list[int], team_id: int, n_each: int = 3) -> tuple[list[dict], list[dict]]:
    if not game_ids:
        return [], []
    rows = session.execute(
        select(GameSkaterStat, Player)
        .join(Player, Player.id == GameSkaterStat.player_id)
        .where(
            GameSkaterStat.game_id.in_(game_ids),
            GameSkaterStat.team_id == team_id,
            Player.position != "G",
        )
    ).all()
    agg: dict[int, dict[str, Any]] = {}
    for gs, pl in rows:
        d = agg.setdefault(
            pl.id,
            {
                "player": pl,
                "gp": 0,
                "goals": 0,
                "assists": 0,
                "gr_sum": 0.0,
                "gr_n": 0,
                "pm_sum": 0,
                "pm_n": 0,
                "toi": 0,
            },
        )
        d["gp"] += 1
        d["goals"] += int(gs.goals or 0)
        d["assists"] += int(gs.assists or 0)
        if gs.game_rating is not None:
            d["gr_sum"] += float(gs.game_rating)
            d["gr_n"] += 1
        if gs.plus_minus is not None:
            d["pm_sum"] += int(gs.plus_minus)
            d["pm_n"] += 1
        if gs.toi_seconds:
            d["toi"] += int(gs.toi_seconds)

    candidates: list[tuple[float, dict]] = []
    for pid, d in agg.items():
        if d["gp"] < 2:
            continue
        gr_avg = (d["gr_sum"] / d["gr_n"]) if d["gr_n"] else 0.0
        toi_avg = d["toi"] / max(d["gp"], 1)
        if toi_avg < 180:
            continue
        pl: Player = d["player"]
        pm_avg = (d["pm_sum"] / d["pm_n"]) if d["pm_n"] else None
        photo = _player_photo_url(pl)
        candidates.append(
            (
                gr_avg,
                {
                    "player_id": pl.id,
                    "name": pl.full_name,
                    "pos": (pl.position or "—").strip(),
                    "photo_url": photo or None,
                    "gr": round(gr_avg, 2) if d["gr_n"] else None,
                    "g": d["goals"],
                    "a": d["assists"],
                    "p": d["goals"] + d["assists"],
                    "plus_minus": round(pm_avg, 1) if pm_avg is not None else None,
                    "toi": _fmt_toi(int(toi_avg)),
                },
            )
        )
    candidates.sort(key=lambda x: x[0], reverse=True)
    hot = [x[1] for x in candidates[:n_each]]
    candidates.sort(key=lambda x: x[0])
    cold = [x[1] for x in candidates[:n_each]]
    return hot, cold


def _projected_starter(session, season_id: int, team_id: int) -> dict[str, Any] | None:
    row = session.execute(
        select(PlayerGoalieStat, Player)
        .join(Player, Player.id == PlayerGoalieStat.player_id)
        .where(
            PlayerGoalieStat.season_id == season_id,
            PlayerGoalieStat.team_id == team_id,
            PlayerGoalieStat.stat_segment == "rs",
        )
        .order_by(PlayerGoalieStat.games_started.desc().nulls_last(), PlayerGoalieStat.wins.desc())
    ).first()
    if not row:
        return None
    gs, pl = row
    if not gs.games_started and not gs.gp:
        return None
    photo = _player_photo_url(pl)
    return {
        "player_id": pl.id,
        "name": pl.full_name,
        "photo_url": photo or None,
        "record": f"{int(gs.wins or 0)}-{int(gs.losses or 0)}-{int(gs.otl or 0)}",
        "gaa": round(float(gs.gaa), 2) if gs.gaa is not None else None,
        "sv_pct": round(float(gs.sv_pct), 3) if gs.sv_pct is not None else None,
    }


def _recent_meetings(session, season_id: int, home_id: int, away_id: int, exclude_game_id: int) -> list[dict[str, Any]]:
    q = (
        select(Game)
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.id != exclude_game_id,
            (
                ((Game.home_team_id == home_id) & (Game.away_team_id == away_id))
                | ((Game.home_team_id == away_id) & (Game.away_team_id == home_id))
            ),
        )
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(2)
    )
    out = []
    for g in session.scalars(q).all():
        ht = g.home_team
        at = g.away_team
        note = ""
        if g.went_to_shootout:
            note = "SO"
        elif g.went_to_overtime:
            note = "OT"
        out.append(
            {
                "game_id": g.id,
                "date": g.game_date.isoformat() if g.game_date else None,
                "away_abbr": at.abbreviation if at else "",
                "home_abbr": ht.abbreviation if ht else "",
                "away_score": int(g.away_score) if g.away_score is not None else None,
                "home_score": int(g.home_score) if g.home_score is not None else None,
                "extra": note,
            }
        )
    return out


def _team_card(session, season_id: int, team: Team, anchor: Game, opp: Team) -> dict[str, Any]:
    tid = int(team.id)
    st = _team_standing_row(session, season_id, tid)
    pp_rank, pk_rank = _pp_pk_ranks_for_rs(session, season_id)
    agg = session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season_id,
            TeamSeasonAggregate.team_id == tid,
            TeamSeasonAggregate.stat_segment == "rs",
        )
    ).first()
    pp_pct = pk_pct = None
    if agg:
        if agg.pp_chances and agg.pp_goals is not None and agg.pp_chances > 0:
            pp_pct = round(100.0 * float(agg.pp_goals) / float(agg.pp_chances), 1)
        if agg.sh_chances and agg.pk_goals_against is not None and agg.sh_chances > 0:
            pk_pct = round(
                100.0 - (100.0 * float(agg.pk_goals_against) / float(agg.sh_chances)),
                1,
            )
    recent = _games_before(session, season_id, tid, anchor)
    l10 = _last_10_record(recent, tid)
    gids = [g.id for g in recent]
    hot, cold = _hot_cold_skaters(session, gids, tid)
    starter = _projected_starter(session, season_id, tid)
    record = None
    if st:
        record = {
            "pts": int(st.pts or 0),
            "w": int(st.w or 0),
            "l": int(st.l or 0),
            "otl": int(st.otl or 0),
            "str": f"{int(st.w or 0)}-{int(st.l or 0)}-{int(st.otl or 0)}",
        }
    streak = (st.streak or "").strip() if st else ""
    season_h2h = _season_h2h_vs_opponent(session, season_id, tid, opp, int(anchor.id))
    return {
        "team": {
            "id": team.id,
            "name": team.name,
            "abbreviation": team.abbreviation,
            "slug": team.slug,
            "logo_url": team_logo_url_for_team(team),
            "display_name": team.full_display_name(),
        },
        "opponent": {
            "abbreviation": opp.abbreviation,
            "name": opp.name,
            "slug": opp.slug,
        },
        "record": record,
        "streak": streak or None,
        "standing_line": _standing_blurb(session, season_id, tid),
        "pp_pct": pp_pct,
        "pp_rank": pp_rank.get(tid),
        "pk_pct": pk_pct,
        "pk_rank": pk_rank.get(tid),
        "last_10": l10,
        "season_h2h": season_h2h,
        "hot": hot,
        "cold": cold,
        "projected_starter": starter,
    }


def game_preview_payload(game_id: int) -> dict[str, Any] | None:
    """JSON for pre-game preview. Returns None if game missing."""
    session = db.session
    game = session.get(Game, game_id)
    if not game:
        return None
    if (game.status or "").lower() == "final":
        return {"error": "final", "message": "Use boxscore for completed games."}

    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    if not home or not away:
        return {"error": "teams", "message": "Missing team data."}

    sid = int(game.season_id)
    rs_map = load_rs_strength_by_team(session, sid)
    h2h = load_rs_head_to_head(session, sid)
    hid, aid = int(game.home_team_id), int(game.away_team_id)
    rh = _team_rating(rs_map, hid)
    ra = _team_rating(rs_map, aid)
    z = (rh - ra) * 0.11 + 0.12 * _h2h_per_game_margin_for_team_a(h2h, hid, aid) + 0.42
    p_home = _logistic_stable(z)
    p_home = min(0.88, max(0.12, float(p_home)))

    meetings = _recent_meetings(session, sid, hid, aid, int(game.id))
    away_card = _team_card(session, sid, away, game, home)
    home_card = _team_card(session, sid, home, game, away)

    return {
        "game_id": game.id,
        "status": game.status,
        "date": game.game_date.isoformat() if game.game_date else None,
        "game_type": game.game_type,
        "arena": game.arena,
        "prediction_method_note": PREVIEW_METHOD_NOTE,
        "odds": {
            "home_win_prob": round(p_home, 4),
            "away_win_prob": round(1.0 - p_home, 4),
            "home_pct_display": round(100.0 * p_home, 1),
            "away_pct_display": round(100.0 * (1.0 - p_home), 1),
            "method_note": PREVIEW_METHOD_NOTE,
        },
        "recent_meetings": meetings,
        "away": away_card,
        "home": home_card,
        "injuries_note": "Injury reports are not included in current league data imports.",
    }
