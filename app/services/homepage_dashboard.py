"""Homepage dashboard payload builders (games, standings, trends, power ranks)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from flask import current_app, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.logo_urls import team_logo_url_for_team
from app.services.division_labels import division_group_key_for_standing, team_division_display_label
from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryChampion,
    Player,
    PlayerSkaterStat,
    Season,
    Team,
    TeamStanding,
)

_BANNER_FILE_RE = re.compile(r"^banner\s*(\d+)\.(png|webp|jpe?g)$", re.IGNORECASE)
_BANNER_EXT_PRIORITY = {".png": 0, ".webp": 1, ".jpeg": 2, ".jpg": 2}


def league_calendar_anchor_date(session, season_id: int) -> date:
    """League 'today' for rolling windows: latest **completed** game date in the season.

    Sim seasons use in-world game dates (not the real-world clock). Falls back to any
    scheduled game's max date if nothing is final yet, then real-world today if the season
    has no dated games.
    """
    anchor = session.scalar(
        select(func.max(Game.game_date)).where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.game_date.is_not(None),
        )
    )
    if anchor:
        return anchor
    anchor2 = session.scalar(
        select(func.max(Game.game_date)).where(
            Game.season_id == season_id,
            Game.game_date.is_not(None),
        )
    )
    if anchor2:
        return anchor2
    return date.today()


def _champion_banner_urls() -> list[str]:
    """Same logic as main.champion_banner_urls; avoids circular import at module load."""
    from app.config import BASE_DIR

    rel = str(current_app.config.get("HISTORY_CHAMPIONS_REL_DIR", "img/history/champions")).strip("/\\")
    primary_dir = (Path(current_app.root_path) / "static" / Path(rel)).resolve()
    legacy_rel = "img/history/champions"
    legacy_dir = (Path(current_app.root_path) / "static" / legacy_rel).resolve()

    def _scan(folder: Path) -> dict[int, str]:
        if not folder.is_dir():
            return {}
        by_n: dict[int, tuple[int, str]] = {}
        for p in folder.iterdir():
            if not p.is_file():
                continue
            safe_name = unicodedata.normalize("NFC", p.name)
            m = _BANNER_FILE_RE.match(safe_name)
            if not m:
                continue
            n = int(m.group(1))
            ext = p.suffix.lower()
            prio = _BANNER_EXT_PRIORITY.get(ext, 9)
            prev = by_n.get(n)
            if prev is None or prio < prev[0]:
                by_n[n] = (prio, p.name)
        return {n: name for n, (_, name) in by_n.items()}

    merged: dict[int, tuple[str, str]] = {}
    if primary_dir != legacy_dir:
        for n, name in _scan(legacy_dir).items():
            merged[n] = (legacy_rel, name)
    for n, name in _scan(primary_dir).items():
        merged[n] = (rel, name)
    ordered = sorted(merged.items(), key=lambda kv: kv[0])
    return [url_for("static", filename=f"{out_rel}/{name}") for _, (out_rel, name) in ordered]


def standing_row_json(
    st: TeamStanding, tm: Team, rank: int, *, display_division: str | None = None
) -> dict[str, Any]:
    div_out = (display_division or "").strip() or None
    if div_out is None:
        div_out = (st.division or "").strip() or None
    return {
        "rank": rank,
        "slug": tm.slug,
        "name": tm.full_display_name(),
        "abbr": tm.abbreviation,
        "logo_url": team_logo_url_for_team(tm),
        "gp": st.standing_gp_display(),
        "w": st.w,
        "l": st.l,
        "ties": int(st.ties or 0),
        "pts": st.pts,
        "conference": (st.conference or "").strip() or None,
        "division": div_out,
    }


def build_standings_by_division(
    session,
    season_id: int,
    *,
    div_name_by_pair: dict[tuple[int, int], str] | None = None,
    div_name_by_id: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    pair = div_name_by_pair or {}
    idm = div_name_by_id or {}
    rows = session.scalars(
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season_id)
        .order_by(TeamStanding.pts.desc())
    ).all()
    by_div: dict[str, list[tuple[TeamStanding, Team]]] = defaultdict(list)
    for st in rows:
        tm = st.team
        if not tm:
            continue
        key = division_group_key_for_standing(st, tm, pair, idm)
        by_div[key].append((st, tm))
    out: list[dict[str, Any]] = []
    for div_name in sorted(by_div.keys(), key=lambda x: (x == "League", x)):
        group = by_div[div_name]
        group.sort(key=lambda x: (-x[0].pts, -x[0].w, x[1].name or ""))
        teams_out = []
        for i, (st, tm) in enumerate(group, start=1):
            disp = (team_division_display_label(st, tm, pair, idm) or "").strip() or None
            teams_out.append(standing_row_json(st, tm, i, display_division=disp))
        out.append({"division": div_name, "teams": teams_out})
    return out


def _conf_playoff_cutoff_pts(group: list[TeamStanding]) -> int:
    """Points held by team at playoff line (8th or last in small leagues)."""
    if not group:
        return 0
    sorted_pts = sorted([st.pts for st in group], reverse=True)
    idx = min(7, len(sorted_pts) - 1)
    return int(sorted_pts[idx])


def _playoff_stake_for_game(
    g: Game,
    st_map: dict[int, TeamStanding],
    tm_map: dict[int, Team],
    conf_cutoff: dict[str, int],
) -> float:
    sh = st_map.get(g.home_team_id)
    sa = st_map.get(g.away_team_id)
    if not sh or not sa:
        return 0.0
    ch = (sh.conference or "").strip() or "League"
    ca = (sa.conference or "").strip() or "League"
    score = 0.0
    if ch == ca:
        score += 28.0
        if (sh.division or "").strip() == (sa.division or "").strip() and (sh.division or "").strip():
            score += 18.0
    ph, pa = int(sh.pts or 0), int(sa.pts or 0)
    score += max(0.0, 22.0 - abs(ph - pa) * 0.8)
    if ch == ca:
        cutoff = float(conf_cutoff.get(ch, max(ph, pa)))
        dh = abs(float(ph) - cutoff)
        da = abs(float(pa) - cutoff)
        score += max(0.0, 35.0 - min(dh, da) * 1.2)
    if g.status == "final":
        hs, as_ = int(g.home_score or 0), int(g.away_score or 0)
        if abs(hs - as_) <= 1:
            score += 14.0
    return score


def _game_card_json(session, g: Game) -> dict[str, Any]:
    ht = session.get(Team, g.home_team_id)
    at = session.get(Team, g.away_team_id)
    return {
        "id": g.id,
        "date": g.game_date.isoformat() if g.game_date else None,
        "status": g.status or "",
        "game_type": g.game_type or "",
        "home_score": g.home_score,
        "away_score": g.away_score,
        "home_name": ht.full_display_name() if ht else "",
        "away_name": at.full_display_name() if at else "",
        "home_abbr": ht.abbreviation if ht else "",
        "away_abbr": at.abbreviation if at else "",
        "home_slug": ht.slug if ht else "",
        "away_slug": at.slug if at else "",
        "home_logo_url": team_logo_url_for_team(ht) if ht else "",
        "away_logo_url": team_logo_url_for_team(at) if at else "",
    }


def pick_game_of_the_night(
    session,
    season_id: int,
    st_map: dict[int, TeamStanding],
    tm_map: dict[int, Team],
    conf_cutoff: dict[str, int],
    since: date | None = None,
) -> dict[str, Any] | None:
    """Pick highest-stakes final in season. Optional ``since`` lower-bounds game_date (sim seasons may be historical dates)."""
    q = select(Game).where(
        Game.season_id == season_id,
        Game.status == "final",
        Game.game_date.is_not(None),
    )
    if since is not None:
        q = q.where(Game.game_date >= since)
    games = session.scalars(q.order_by(Game.game_date.desc(), Game.id.desc()).limit(120)).all()
    if not games:
        return None
    scored: list[tuple[float, Game]] = []
    for g in games:
        s = _playoff_stake_for_game(g, st_map, tm_map, conf_cutoff)
        scored.append((s, g))
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    if not scored or scored[0][0] <= 0:
        top = max(scored, key=lambda x: x[0]) if scored else None
        if not top or top[0] <= 0:
            g = games[0]
        else:
            g = top[1]
    else:
        top_score = scored[0][0]
        tied = [g for sc, g in scored if sc >= top_score - 1.0][:5]
        h = hashlib.md5(f"{season_id}-{tied[0].id}".encode(), usedforsecurity=False).hexdigest()
        pick = int(h[:8], 16) % len(tied)
        g = tied[pick]
    out = _game_card_json(session, g)
    out["stake_score"] = round(_playoff_stake_for_game(g, st_map, tm_map, conf_cutoff), 2)
    return out


def pick_next_game_to_watch(
    session,
    season_id: int,
    st_map: dict[int, TeamStanding],
    tm_map: dict[int, Team],
    conf_cutoff: dict[str, int],
    from_date: date,
) -> dict[str, Any] | None:
    games = session.scalars(
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status != "final",
            Game.game_date.is_not(None),
            Game.game_date >= from_date,
        )
        .order_by(Game.game_date.asc(), Game.id.asc())
        .limit(80)
    ).all()
    if not games:
        games = session.scalars(
            select(Game)
            .where(Game.season_id == season_id, Game.status != "final")
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            .limit(80)
        ).all()
    if not games:
        return None
    best: tuple[float, Game] | None = None
    for g in games:
        s = _playoff_stake_for_game(g, st_map, tm_map, conf_cutoff)
        if best is None or s > best[0]:
            best = (s, g)
    g = best[1] if best else games[0]
    out = _game_card_json(session, g)
    out["stake_score"] = round(_playoff_stake_for_game(g, st_map, tm_map, conf_cutoff), 2)
    return out


def _player_photo_url(pl: Player) -> str:
    from app.services.player_headshot import resolve_player_headshot_static_filename

    static_root = Path(current_app.root_path) / "static"
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    return url_for("static", filename=rel) if rel else ""


def _stars_skaters_in_window(session, season_id: int, start: date, end: date, limit: int = 3) -> list[dict[str, Any]]:
    game_ids = session.scalars(
        select(Game.id).where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.game_date.is_not(None),
            Game.game_date >= start,
            Game.game_date <= end,
        )
    ).all()
    if not game_ids:
        return []
    gid_set = [int(x) for x in game_ids]
    rows = session.execute(
        select(GameSkaterStat, Game, Player, Team)
        .join(Game, GameSkaterStat.game_id == Game.id)
        .join(Player, GameSkaterStat.player_id == Player.id)
        .outerjoin(Team, GameSkaterStat.team_id == Team.id)
        .where(GameSkaterStat.game_id.in_(gid_set))
    ).all()
    pts_by_player: dict[int, dict[str, Any]] = defaultdict(lambda: {"g": 0, "a": 0, "gp_set": set()})
    for gss, _g, pl, tm in rows:
        pid = pl.id
        pts_by_player[pid]["player_id"] = pid
        pts_by_player[pid]["player"] = pl.full_name
        pts_by_player[pid]["player_photo_url"] = _player_photo_url(pl)
        pts_by_player[pid]["team"] = tm.abbreviation if tm else ""
        pts_by_player[pid]["team_slug"] = tm.slug if tm else ""
        pts_by_player[pid]["team_logo_url"] = team_logo_url_for_team(tm) if tm else ""
        pts_by_player[pid]["g"] += int(gss.goals or 0)
        pts_by_player[pid]["a"] += int(gss.assists or 0)
        pts_by_player[pid]["gp_set"].add(gss.game_id)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for pid, d in pts_by_player.items():
        gp = len(d["gp_set"])
        p = int(d["g"]) + int(d["a"])
        scored.append((p, gp, d))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2].get("player") or ""))
    out: list[dict[str, Any]] = []
    for p, gp, d in scored[:limit]:
        d2 = {k: v for k, v in d.items() if k != "gp_set"}
        d2["points"] = p
        d2["games"] = gp
        out.append(d2)
    return out


def build_stars_windows(session, season_id: int, as_of: date) -> dict[str, list[dict[str, Any]]]:
    """Rolling N **league** days ending inclusive on ``as_of`` (see ``league_calendar_anchor_date``)."""
    return {
        "stars_last_7d": _stars_skaters_in_window(session, season_id, as_of - timedelta(days=7), as_of),
        "stars_last_14d": _stars_skaters_in_window(session, season_id, as_of - timedelta(days=14), as_of),
        "stars_last_30d": _stars_skaters_in_window(session, season_id, as_of - timedelta(days=30), as_of),
    }


def build_trending_players(
    session,
    season_id: int,
    segment: str,
    as_of: date,
    window_days: int = 14,
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Recent form vs season baseline using the last ``window_days`` **league** days ending ``as_of``."""
    start = as_of - timedelta(days=window_days)
    game_ids = [
        int(x)
        for x in session.scalars(
            select(Game.id).where(
                Game.season_id == season_id,
                Game.status == "final",
                Game.game_date.is_not(None),
                Game.game_date >= start,
                Game.game_date <= as_of,
            )
        ).all()
    ]
    recent_pts: dict[int, float] = defaultdict(float)
    recent_gp: dict[int, set[int]] = defaultdict(set)
    if game_ids:
        rows = session.execute(
            select(GameSkaterStat.player_id, GameSkaterStat.game_id, GameSkaterStat.goals, GameSkaterStat.assists).where(
                GameSkaterStat.game_id.in_(game_ids)
            )
        ).all()
        for pid, gid, g, a in rows:
            recent_pts[int(pid)] += float((g or 0) + (a or 0))
            recent_gp[int(pid)].add(int(gid))
    season_rows = session.execute(
        select(PlayerSkaterStat.player_id, PlayerSkaterStat.points, PlayerSkaterStat.gp, Player)
        .join(Player, PlayerSkaterStat.player_id == Player.id)
        .where(PlayerSkaterStat.season_id == season_id, PlayerSkaterStat.stat_segment == segment)
    ).all()
    deltas: list[tuple[float, Player, int, float, float]] = []
    for pid, pts, gp, pl in season_rows:
        rgp = len(recent_gp.get(int(pid), set()))
        if rgp < 3:
            continue
        sgp = max(int(gp or 0), 1)
        season_ppg = float(pts or 0) / sgp
        recent_ppg = float(recent_pts.get(int(pid), 0.0)) / max(rgp, 1)
        deltas.append((recent_ppg - season_ppg, pl, rgp, recent_ppg, season_ppg))
    sorted_hot = sorted(deltas, key=lambda x: (-x[0], -x[2], x[1].full_name))
    hot: list[dict[str, Any]] = []
    for dlt, pl, rgp, rppg, sppg in sorted_hot[:limit]:
        tm = session.get(Team, pl.current_team_id) if pl.current_team_id else None
        hot.append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "player_photo_url": _player_photo_url(pl),
                "team": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "recent_games": rgp,
                "recent_ppg": round(rppg, 3),
                "season_ppg": round(sppg, 3),
                "delta": round(dlt, 3),
            }
        )
    sorted_cold = sorted(deltas, key=lambda x: (x[0], -x[2], x[1].full_name))
    cold: list[dict[str, Any]] = []
    for dlt, pl, rgp, rppg, sppg in sorted_cold[:limit]:
        tm = session.get(Team, pl.current_team_id) if pl.current_team_id else None
        cold.append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "player_photo_url": _player_photo_url(pl),
                "team": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "recent_games": rgp,
                "recent_ppg": round(rppg, 3),
                "season_ppg": round(sppg, 3),
                "delta": round(dlt, 3),
            }
        )
    return {"hot": hot, "cold": cold}


def build_active_streaks(session, season_id: int, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Goal streak / point streak from most recent games backward (skaters only)."""
    games = session.scalars(
        select(Game)
        .where(Game.season_id == season_id, Game.status == "final")
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(400)
    ).all()
    game_ids = [g.id for g in games]
    if not game_ids:
        return {"goal_streak": [], "point_streak": []}
    rows = session.execute(
        select(GameSkaterStat, Game, Player, Team)
        .join(Game, GameSkaterStat.game_id == Game.id)
        .join(Player, GameSkaterStat.player_id == Player.id)
        .outerjoin(Team, GameSkaterStat.team_id == Team.id)
        .where(GameSkaterStat.game_id.in_(game_ids))
    ).all()
    by_player: dict[int, list[tuple[Game, GameSkaterStat, Player, Team | None]]] = defaultdict(list)
    for gss, g, pl, tm in rows:
        by_player[pl.id].append((g, gss, pl, tm))
    for pid in by_player:
        by_player[pid].sort(key=lambda x: (x[0].game_date or date.min, x[0].id), reverse=True)

    goal_best: list[tuple[int, Player, Team | None]] = []
    point_best: list[tuple[int, Player, Team | None]] = []
    for _pid, lst in by_player.items():
        if not lst:
            continue
        pl = lst[0][2]
        tm = lst[0][3]
        gl = 0
        for _g, gss, _, _ in lst:
            if int(gss.goals or 0) > 0:
                gl += 1
            else:
                break
        pt = 0
        for _g, gss, _, _ in lst:
            if int(gss.goals or 0) + int(gss.assists or 0) > 0:
                pt += 1
            else:
                break
        if gl >= 2:
            goal_best.append((gl, pl, tm))
        if pt >= 2:
            point_best.append((pt, pl, tm))
    goal_best.sort(key=lambda x: (-x[0], x[1].full_name))
    point_best.sort(key=lambda x: (-x[0], x[1].full_name))

    def pack(lst: list[tuple[int, Player, Team | None]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for n, pl, tm in lst[:limit]:
            out.append(
                {
                    "player_id": pl.id,
                    "player": pl.full_name,
                    "player_photo_url": _player_photo_url(pl),
                    "streak": n,
                    "team": tm.abbreviation if tm else "",
                    "team_slug": tm.slug if tm else "",
                    "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                }
            )
        return out

    return {"goal_streak": pack(goal_best), "point_streak": pack(point_best)}


def _team_game_points(team_id: int, g: Game) -> int | None:
    """Standings-style points from a final game (2 / 1 OTL / 1 tie / 0 regulation loss)."""
    if g.status != "final" or g.home_score is None or g.away_score is None:
        return None
    hs, aws = int(g.home_score), int(g.away_score)
    tid = int(team_id)
    ot = bool(g.went_to_overtime or g.went_to_shootout)
    if tid == int(g.home_team_id):
        if hs > aws:
            return 2
        if hs < aws:
            return 1 if ot else 0
        return 1
    if tid == int(g.away_team_id):
        if aws > hs:
            return 2
        if aws < hs:
            return 1 if ot else 0
        return 1
    return None


def _team_game_outcome_streak(team_id: int, g: Game) -> str | None:
    """Single-letter result for streak logic: win, tie, or loss (OTL is a loss)."""
    if g.status != "final" or g.home_score is None or g.away_score is None:
        return None
    hs, aws = int(g.home_score), int(g.away_score)
    tid = int(team_id)
    if tid == int(g.home_team_id):
        if hs > aws:
            return "W"
        if hs < aws:
            return "L"
        return "T"
    if tid == int(g.away_team_id):
        if aws > hs:
            return "W"
        if aws < hs:
            return "L"
        return "T"
    return None


def _team_trending_row(
    dlt: float,
    tm: Team,
    rgp: int,
    rppg: float,
    sppg: float,
) -> dict[str, Any]:
    return {
        "team_id": tm.id,
        "team": tm.abbreviation or "",
        "team_name": tm.full_display_name(),
        "team_city": (tm.city or "").strip(),
        "team_slug": tm.slug or "",
        "team_logo_url": team_logo_url_for_team(tm),
        "recent_games": rgp,
        "recent_ppg": round(rppg, 3),
        "season_ppg": round(sppg, 3),
        "delta": round(dlt, 3),
    }


def build_trending_teams(
    session,
    season_id: int,
    as_of: date,
    window_days: int = 14,
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Team points pace in the last ``window_days`` league days vs full-season pace (like skater trending)."""
    start = as_of - timedelta(days=window_days)
    window_games = session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.game_date.is_not(None),
            Game.game_date >= start,
            Game.game_date <= as_of,
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
    ).all()
    recent_pts: dict[int, float] = defaultdict(float)
    recent_gp: dict[int, int] = defaultdict(int)
    for g in window_games:
        for tid in (int(g.home_team_id), int(g.away_team_id)):
            pts = _team_game_points(tid, g)
            if pts is None:
                continue
            recent_pts[tid] += float(pts)
            recent_gp[tid] += 1

    standings = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    deltas: list[tuple[float, Team, TeamStanding, int, float, float]] = []
    for st in standings:
        tm = session.get(Team, st.team_id)
        if not tm:
            continue
        rgp = int(recent_gp.get(int(st.team_id), 0))
        if rgp < 3:
            continue
        sgp = max(int(st.standing_gp_display() or 0), 1)
        season_ppg = float(st.pts or 0) / float(sgp)
        recent_ppg = recent_pts.get(int(st.team_id), 0.0) / float(rgp)
        deltas.append((recent_ppg - season_ppg, tm, st, rgp, recent_ppg, season_ppg))

    sorted_hot = sorted(deltas, key=lambda x: (-x[0], -x[3], x[1].name or ""))
    hot = [
        _team_trending_row(dlt, tm, rgp, rppg, sppg)
        for dlt, tm, st, rgp, rppg, sppg in sorted_hot[:limit]
    ]
    sorted_cold = sorted(deltas, key=lambda x: (x[0], -x[3], x[1].name or ""))
    cold = [
        _team_trending_row(dlt, tm, rgp, rppg, sppg)
        for dlt, tm, st, rgp, rppg, sppg in sorted_cold[:limit]
    ]
    return {"hot": hot, "cold": cold}


def _team_streak_pack(scored: list[tuple[int, Team]], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n, tm in scored[:limit]:
        out.append(
            {
                "team_id": tm.id,
                "team": tm.abbreviation or "",
                "team_name": tm.full_display_name(),
                "team_city": (tm.city or "").strip(),
                "team_slug": tm.slug or "",
                "team_logo_url": team_logo_url_for_team(tm),
                "streak": n,
            }
        )
    return out


def build_team_momentum_streaks(session, season_id: int, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Current win, undefeated (W + ties), losing, and winless (L + ties) streaks from most recent games backward."""
    games = session.scalars(
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.game_date.is_not(None),
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
        .order_by(Game.game_date.asc(), Game.id.asc())
    ).all()
    if not games:
        return {
            "win_streak": [],
            "undefeated_streak": [],
            "losing_streak": [],
            "winless_streak": [],
        }

    by_team: dict[int, list[Game]] = defaultdict(list)
    for g in games:
        by_team[int(g.home_team_id)].append(g)
        by_team[int(g.away_team_id)].append(g)

    def run_length(seq: list[str], pred) -> int:
        n = 0
        for ch in seq:
            if pred(ch):
                n += 1
            else:
                break
        return n

    win_scored: list[tuple[int, Team]] = []
    und_scored: list[tuple[int, Team]] = []
    loss_scored: list[tuple[int, Team]] = []
    winless_scored: list[tuple[int, Team]] = []

    for tid, glist in by_team.items():
        tm = session.get(Team, tid)
        if not tm:
            continue
        recent_first = [_team_game_outcome_streak(tid, g) for g in reversed(glist)]
        recent_first = [x for x in recent_first if x]
        if not recent_first:
            continue
        wn = run_length(recent_first, lambda c: c == "W")
        un = run_length(recent_first, lambda c: c in ("W", "T"))
        ln = run_length(recent_first, lambda c: c == "L")
        wln = run_length(recent_first, lambda c: c in ("L", "T"))
        if wn >= 2:
            win_scored.append((wn, tm))
        if un >= 2:
            und_scored.append((un, tm))
        if ln >= 2:
            loss_scored.append((ln, tm))
        if wln >= 2:
            winless_scored.append((wln, tm))

    win_scored.sort(key=lambda x: (-x[0], x[1].name or ""))
    und_scored.sort(key=lambda x: (-x[0], x[1].name or ""))
    loss_scored.sort(key=lambda x: (-x[0], x[1].name or ""))
    winless_scored.sort(key=lambda x: (-x[0], x[1].name or ""))

    return {
        "win_streak": _team_streak_pack(win_scored, limit),
        "undefeated_streak": _team_streak_pack(und_scored, limit),
        "losing_streak": _team_streak_pack(loss_scored, limit),
        "winless_streak": _team_streak_pack(winless_scored, limit),
    }


def build_power_rankings(
    session,
    season_id: int,
    standings_by_team: dict[int, TeamStanding],
    special_teams: list[dict[str, Any]],
    recent_form: dict[int, dict[str, int | str]],
    _segment: str,
) -> dict[str, list[dict[str, Any]]]:
    st_net = {str(r.get("team")): float(r.get("net_st") or 0) for r in special_teams if r.get("team")}
    team_ids = list(standings_by_team.keys())
    if not team_ids:
        return {"top5": [], "bottom5": []}

    # Opponent avg pts in last up to 10 games per team
    games = session.scalars(
        select(Game)
        .where(Game.season_id == season_id, Game.status == "final")
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(600)
    ).all()
    opp_pts_lists: dict[int, list[int]] = defaultdict(list)
    for g in games:
        if not g.home_team_id or not g.away_team_id:
            continue
        h_st = standings_by_team.get(g.home_team_id)
        a_st = standings_by_team.get(g.away_team_id)
        if not h_st or not a_st:
            continue
        if len(opp_pts_lists[g.home_team_id]) < 10:
            opp_pts_lists[g.home_team_id].append(int(a_st.pts or 0))
        if len(opp_pts_lists[g.away_team_id]) < 10:
            opp_pts_lists[g.away_team_id].append(int(h_st.pts or 0))

    scores: list[tuple[float, Team, TeamStanding]] = []
    for tid, st in standings_by_team.items():
        tm = session.get(Team, tid)
        if not tm:
            continue
        gp = max(int(st.standing_gp_display() or 0), 1)
        form = recent_form.get(tid, {})
        w10 = int(form.get("last10_wins", 0) or 0)
        l10 = int(form.get("last10_losses", 0) or 0)
        form_pct = w10 / max(w10 + l10, 1)
        gd_rate = (float(st.gf or 0) - float(st.ga or 0)) / gp
        net = st_net.get(tm.abbreviation, 0.0)
        opps = opp_pts_lists.get(tid, [])
        sos = sum(opps) / max(len(opps), 1) if opps else float(st.pts or 0)
        raw = (
            0.32 * form_pct * 100.0
            + 0.28 * max(min(gd_rate * 8.0 + 50.0, 100.0), 0.0)
            + 0.22 * min(max(net, 0.0), 120.0) * 0.6
            + 0.18 * min(sos, 120.0)
        )
        scores.append((raw, tm, st))
    scores.sort(key=lambda x: (-x[0], -(x[2].pts or 0), x[1].name or ""))

    def row(tm: Team, st: TeamStanding, pr: float) -> dict[str, Any]:
        return {
            "slug": tm.slug,
            "name": tm.full_display_name(),
            "abbr": tm.abbreviation,
            "logo_url": team_logo_url_for_team(tm),
            "pts": st.pts,
            "gp": st.standing_gp_display(),
            "power_score": round(pr, 2),
        }

    top5 = [row(tm, st, pr) for pr, tm, st in scores[:5]]
    bottom5 = [row(tm, st, pr) for pr, tm, st in scores[-5:][::-1]]
    return {"top5": top5, "bottom5": bottom5}


def build_champions_panel(session) -> dict[str, Any]:
    banners = _champion_banner_urls()
    champs = session.scalars(
        select(HistoryChampion)
        .order_by(HistoryChampion.season_id.desc(), HistoryChampion.id.desc())
        .limit(12)
    ).all()
    slides: list[dict[str, Any]] = []
    for hc in champs:
        tm = session.get(Team, hc.team_id) if hc.team_id else None
        se = session.get(Season, hc.season_id) if hc.season_id else None
        slides.append(
            {
                "season_label": se.label if se else "",
                "trophy": hc.trophy or "",
                "team_name": tm.full_display_name() if tm else "",
                "team_slug": tm.slug if tm else "",
                "logo_url": team_logo_url_for_team(tm) if tm else "",
            }
        )
    return {"banner_urls": banners, "recent_champions": slides}


def build_around_the_league(league_session) -> dict[str, Any]:
    """Published news from site DB; ``league_session`` resolves team slugs/names."""
    from flask import current_app, url_for

    from app.league_db import db
    from app.services.news_categories import news_category_label
    from app.site_models import NewsArticle, User

    slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    rows = db.session.scalars(
        select(NewsArticle)
        .where(NewsArticle.league_slug == slug, NewsArticle.status == "published")
        .order_by(NewsArticle.published_at.desc().nulls_last(), NewsArticle.id.desc())
        .limit(12)
    ).all()
    if not rows:
        return {
            "enabled": False,
            "message": "No league headlines yet.",
            "articles": [],
        }
    author_ids = {a.author_user_id for a in rows}
    authors: dict[int, User] = {}
    if author_ids:
        for u in db.session.scalars(select(User).where(User.id.in_(author_ids))).all():
            authors[u.id] = u

    def _gm_label(u: User | None) -> str:
        if not u:
            return ""
        for attr in ("discord_name", "username"):
            v = (getattr(u, attr, None) or "").strip()
            if v:
                return v
        em = (u.email or "").strip()
        return em.split("@", 1)[0] if em else ""

    articles: list[dict[str, Any]] = []
    for a in rows:
        tm = league_session.get(Team, a.team_id) if a.team_id else None
        excerpt = (a.body or "").strip().replace("\r\n", "\n")
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "…"
        au = authors.get(a.author_user_id)
        articles.append(
            {
                "id": a.id,
                "title": a.title,
                "excerpt": excerpt,
                "category_label": news_category_label(getattr(a, "category", None)),
                "team_name": tm.full_display_name() if tm else None,
                "team_slug": tm.slug if tm else None,
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "gm_label": _gm_label(au),
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "href": url_for("main.league_headlines") + f"#a{a.id}",
            }
        )
    return {"enabled": True, "message": "", "articles": articles}


def build_conf_cutoff_map(session, season_id: int) -> dict[str, int]:
    rows = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    by_conf: dict[str, list[TeamStanding]] = defaultdict(list)
    for st in rows:
        key = (st.conference or "").strip() or "League"
        by_conf[key].append(st)
    return {k: _conf_playoff_cutoff_pts(v) for k, v in by_conf.items()}


def build_team_standing_maps(session, season_id: int) -> tuple[dict[int, TeamStanding], dict[int, Team]]:
    rows = session.scalars(select(TeamStanding).where(TeamStanding.season_id == season_id)).all()
    st_map: dict[int, TeamStanding] = {}
    tm_map: dict[int, Team] = {}
    for st in rows:
        st_map[st.team_id] = st
        tm = session.get(Team, st.team_id)
        if tm:
            tm_map[st.team_id] = tm
    return st_map, tm_map
