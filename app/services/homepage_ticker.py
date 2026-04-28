"""Randomized homepage ticker lines built from the same JSON payload as /api/homepage/summary."""

from __future__ import annotations

import random
from datetime import date
from typing import Any


def _item(text: str, logo_url: str = "", href: str = "") -> dict[str, str]:
    return {"text": text.strip(), "logo_url": str(logo_url or ""), "href": str(href or "")}


def _flatten_standings(standings_by_division: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for div in standings_by_division or []:
        if not isinstance(div, dict):
            continue
        for t in div.get("teams") or []:
            if isinstance(t, dict):
                rows.append(t)
    return rows


def _best_pts_team(standings_by_division: list[Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_pts = -1
    for t in _flatten_standings(standings_by_division):
        pts = int(t.get("pts") or 0)
        if pts > best_pts:
            best_pts = pts
            best = t
    return best


def _fmt_delta(d: Any) -> str:
    try:
        x = float(d)
    except (TypeError, ValueError):
        return ""
    return f"{x:+.3f}"


def build_homepage_ticker_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return a shuffled subset of ticker lines. Payload matches homepage summary JSON."""
    r = random.Random()
    candidates: list[dict[str, str]] = []

    league = payload.get("league") or {}
    league_name = str(league.get("name") or "").strip()
    seg = str(payload.get("segment") or "rs").strip().lower() or "rs"

    if league_name:
        candidates.append(
            _item(f"{league_name} · viewing {seg.upper()} stats on this homepage.", "")
        )
    cal_raw = payload.get("league_calendar_date")
    if cal_raw:
        try:
            d = date.fromisoformat(str(cal_raw)[:10])
            candidates.append(_item(f"League calendar date: {d.strftime('%b %d, %Y')}.", ""))
        except ValueError:
            pass

    best = _best_pts_team(payload.get("standings_by_division") or [])
    if best and best.get("name"):
        slug = str(best.get("slug") or "")
        pts = int(best.get("pts") or 0)
        gp = best.get("gp")
        gp_bit = f", {gp} GP" if gp is not None else ""
        candidates.append(
            _item(
                f"Standings pace: {best.get('name')} leads the board at {pts} pts{gp_bit}.",
                str(best.get("logo_url") or ""),
                f"/team/{slug}" if slug else "",
            )
        )

    leaders = payload.get("leaders") or {}
    leader_specs: list[tuple[str, str, Any]] = [
        ("goals", "Goals leader", lambda row: f"{row.get('player')} — {row.get('value')} G"),
        ("assists", "Assists leader", lambda row: f"{row.get('player')} — {row.get('value')} A"),
        ("points", "Skater points leader", lambda row: f"{row.get('player')} — {row.get('value')} PTS"),
        (
            "goalie_wins",
            "Goalie wins leader",
            lambda row: f"{row.get('player')} — {row.get('value')} W",
        ),
        (
            "goalie_shutouts",
            "Shutout leader",
            lambda row: f"{row.get('player')} — {row.get('value')} SO",
        ),
    ]
    for key, label, fmt in leader_specs:
        rows = leaders.get(key) or []
        if not rows:
            continue
        row = rows[0]
        if not isinstance(row, dict):
            continue
        logo = str(row.get("team_logo_url") or "")
        href = ""
        if row.get("player_id"):
            href = f"/player/{row['player_id']}"
        elif row.get("team_slug"):
            href = f"/team/{row['team_slug']}"
        candidates.append(_item(f"{label}: {fmt(row)}", logo, href))

    games = payload.get("games") or []
    if games and isinstance(games[0], dict):
        g = games[0]
        ha, aa = g.get("home_abbr"), g.get("away_abbr")
        hs, aws = g.get("home_score"), g.get("away_score")
        if ha and aa and hs is not None and aws is not None:
            hi, ai = int(hs), int(aws)
            logo = ""
            if hi > ai:
                logo = str(g.get("home_logo_url") or "")
            elif ai > hi:
                logo = str(g.get("away_logo_url") or "")
            else:
                logo = str(g.get("home_logo_url") or "")
            wid = g.get("id")
            candidates.append(
                _item(
                    f"Latest final: {ha} {hi}–{ai} {aa}.",
                    logo,
                    f"/game/{wid}" if wid else "",
                )
            )

    for key, prefix in (
        ("game_of_the_night", "Game of the Night"),
        ("next_game_to_watch", "Next game to watch"),
    ):
        gc = payload.get(key)
        if not gc or not isinstance(gc, dict):
            continue
        if not (gc.get("home_abbr") and gc.get("away_abbr")):
            continue
        hs, aws = gc.get("home_score"), gc.get("away_score")
        st = str(gc.get("status") or "")
        if st == "final" and hs is not None and aws is not None:
            text = f"{prefix}: {gc.get('home_abbr')} {int(hs)}–{int(aws)} {gc.get('away_abbr')}."
        else:
            text = f"{prefix}: {gc.get('away_abbr')} @ {gc.get('home_abbr')}."
        logo = str(gc.get("home_logo_url") or "")
        gid = gc.get("id")
        candidates.append(_item(text, logo, f"/game/{gid}" if gid else ""))

    up = payload.get("upcoming") or []
    if up and isinstance(up[0], dict):
        u = up[0]
        ha, aa = u.get("home_abbr"), u.get("away_abbr")
        if ha and aa:
            logo = str(u.get("away_logo_url") or u.get("home_logo_url") or "")
            uid = u.get("id")
            candidates.append(
                _item(
                    f"Coming up: {aa} @ {ha}.",
                    logo,
                    f"/game/{uid}" if uid else "",
                )
            )

    pr = payload.get("power_rankings") or {}
    top5 = pr.get("top5") or []
    if top5 and isinstance(top5[0], dict):
        t1 = top5[0]
        slug = str(t1.get("slug") or "")
        candidates.append(
            _item(
                f"Power ranking #1: {t1.get('name')} (score {t1.get('power_score')}).",
                str(t1.get("logo_url") or ""),
                f"/team/{slug}" if slug else "",
            )
        )

    st_rows = payload.get("special_teams") or []
    if st_rows and isinstance(st_rows[0], dict):
        top = st_rows[0]
        slug = str(top.get("team_slug") or "")
        candidates.append(
            _item(
                f"Special teams edge: {top.get('team_name')} (net ST+ {top.get('net_st')}).",
                str(top.get("team_logo_url") or ""),
                f"/team/{slug}" if slug else "",
            )
        )

    stars = payload.get("stars_last_7d") or []
    if stars and isinstance(stars[0], dict):
        s0 = stars[0]
        href = f"/player/{s0['player_id']}" if s0.get("player_id") else ""
        candidates.append(
            _item(
                f"Last 7 league days: {s0.get('player')} paced the league "
                f"({s0.get('points')} PTS in {s0.get('games')} GP).",
                str(s0.get("team_logo_url") or ""),
                href,
            )
        )

    rook = payload.get("rookies") or {}
    sk = rook.get("skaters") or []
    if sk and isinstance(sk[0], dict):
        rk = sk[0]
        href = f"/player/{rk['player_id']}" if rk.get("player_id") else ""
        candidates.append(
            _item(
                f"Rookie watch: {rk.get('player')} at {rk.get('ppg')} P/GP ({rk.get('points')} PTS).",
                str(rk.get("team_logo_url") or ""),
                href,
            )
        )
    gl = rook.get("goalies") or []
    if gl and isinstance(gl[0], dict):
        rg = gl[0]
        href = f"/player/{rg['player_id']}" if rg.get("player_id") else ""
        sv = rg.get("sv_pct")
        sv_s = f"{float(sv):.3f}" if sv is not None else "—"
        candidates.append(
            _item(
                f"Rookie goalie: {rg.get('player')} — {sv_s} SV%, {rg.get('wins')} W.",
                str(rg.get("team_logo_url") or ""),
                href,
            )
        )

    ch_slides = (payload.get("champions_panel") or {}).get("recent_champions") or []
    if ch_slides:
        pick_n = min(3, len(ch_slides))
        for slide in r.sample(list(ch_slides), pick_n):
            if not isinstance(slide, dict) or not slide.get("team_name"):
                continue
            trophy = str(slide.get("trophy") or "").strip()
            tbit = f"{trophy} — " if trophy else ""
            slug = str(slide.get("team_slug") or "")
            candidates.append(
                _item(
                    f"Champion flashback: {tbit}{slide.get('team_name')} ({slide.get('season_label')}).",
                    str(slide.get("logo_url") or ""),
                    f"/team/{slug}" if slug else "",
                )
            )

    streaks = (payload.get("team_momentum") or {}).get("streaks") or {}
    streak_labels = (
        ("win_streak", "Win streak"),
        ("undefeated_streak", "Undefeated run"),
        ("losing_streak", "Losing skid"),
        ("winless_streak", "Winless stretch"),
    )
    for kind, label in streak_labels:
        rows = streaks.get(kind) or []
        if not rows or not isinstance(rows[0], dict):
            continue
        row = rows[0]
        n = row.get("streak")
        name = row.get("team_name") or row.get("team")
        if n and name:
            slug = str(row.get("team_slug") or "")
            candidates.append(
                _item(
                    f"{label}: {name} ({n} games).",
                    str(row.get("team_logo_url") or ""),
                    f"/team/{slug}" if slug else "",
                )
            )

    ast = payload.get("active_streaks") or {}
    gs = ast.get("goal_streak") or []
    if gs and isinstance(gs[0], dict):
        row = gs[0]
        href = f"/player/{row['player_id']}" if row.get("player_id") else ""
        candidates.append(
            _item(
                f"Goal streak: {row.get('player')} has scored in {row.get('streak')} straight GP.",
                str(row.get("team_logo_url") or ""),
                href,
            )
        )
    ps = ast.get("point_streak") or []
    if ps and isinstance(ps[0], dict):
        row = ps[0]
        href = f"/player/{row['player_id']}" if row.get("player_id") else ""
        candidates.append(
            _item(
                f"Point streak: {row.get('player')} has a point in {row.get('streak')} straight GP.",
                str(row.get("team_logo_url") or ""),
                href,
            )
        )

    hotp = (payload.get("trending_players") or {}).get("hot") or []
    if hotp and isinstance(hotp[0], dict):
        row = hotp[0]
        dlt_s = _fmt_delta(row.get("delta"))
        tail = f" ({dlt_s} PPG vs season)" if dlt_s else ""
        href = f"/player/{row['player_id']}" if row.get("player_id") else ""
        candidates.append(
            _item(
                f"Heating up: {row.get('player')}{tail}.",
                str(row.get("team_logo_url") or ""),
                href,
            )
        )

    hott = (payload.get("team_momentum") or {}).get("trending") or {}
    tmhot = hott.get("hot") or []
    if tmhot and isinstance(tmhot[0], dict):
        row = tmhot[0]
        dlt_s = _fmt_delta(row.get("delta"))
        tail = f" ({dlt_s} PPG vs season pace)" if dlt_s else ""
        slug = str(row.get("team_slug") or "")
        candidates.append(
            _item(
                f"Team trending: {row.get('team_name')}{tail}.",
                str(row.get("team_logo_url") or ""),
                f"/team/{slug}" if slug else "",
            )
        )

    panel = payload.get("identity_panel") or {}
    for it in (panel.get("items") or [])[:3]:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()
        val = str(it.get("value") or "").strip()
        if not label or not val or val == "—":
            continue
        det = str(it.get("detail") or "").strip()
        extra = f" ({det})" if det else ""
        slug = str(it.get("team_slug") or "")
        candidates.append(
            _item(
                f"{label}: {val}{extra}.",
                str(it.get("team_logo_url") or ""),
                f"/team/{slug}" if slug else "",
            )
        )

    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for it in candidates:
        t = it.get("text") or ""
        if t and t not in seen:
            seen.add(t)
            uniq.append(it)

    if not uniq:
        name = league_name or "Boys of Winter"
        return [_item(f"{name}: season snapshot loading — check back soon.", "")]

    r.shuffle(uniq)
    n = min(26, max(10, len(uniq)))
    return uniq[:n]
