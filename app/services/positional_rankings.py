"""League positional strength (F / D / G) by NHL roster + snapshot baselines for standings Δ column."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import Player, Team, db
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.prospect_system_rankings import apply_system_rank_trends
from app.site_models import PositionalRankSnapshot


def _player_ovr(pl: Player) -> int | None:
    rr = get_player_ratings_row(pl.fhm_player_id)
    return compute_player_overall_100(
        pl.overall_ability,
        pl.overall_potential,
        rr,
        is_goalie=player_is_goalie_for_overall(pl),
    )


def _is_forward(pos: str | None) -> bool:
    p = (pos or "").strip().upper()
    return p in ("LW", "RW", "C")


def _is_defense(pos: str | None) -> bool:
    p = (pos or "").strip().upper()
    return p in ("D", "LD", "RD")


def _is_goalie(pos: str | None) -> bool:
    return (pos or "").strip().upper() == "G"


def _avg_top_n_ovrs(players: list[Player], pred, n: int) -> float:
    ovrs: list[int] = []
    for pl in players:
        if not pred(pl.position):
            continue
        o = _player_ovr(pl)
        if o is not None:
            ovrs.append(int(o))
    if not ovrs:
        return float("-inf")
    ovrs.sort(reverse=True)
    take = ovrs[:n]
    return sum(take) / len(take)


def build_positional_ranking_rows(session: object) -> list[dict[str, Any]]:
    """Roster-based paper ranks: F/D/G league ranks from avg OVR of top skaters; overall rank by sum of those ranks."""
    league_ids = frozenset(bowl_nhl_league_ids(session))
    if not league_ids:
        return []
    teams = list(session.scalars(select(Team).where(Team.fhm_league_id.in_(league_ids)).order_by(Team.name)).all())
    if not teams:
        return []
    team_ids = [t.id for t in teams]
    players = list(
        session.scalars(
            select(Player)
            .options(joinedload(Player.current_team))
            .where(
                Player.retired.is_(False),
                Player.current_team_id.in_(team_ids),
            )
        )
        .unique()
        .all()
    )
    by_team: dict[int, list[Player]] = {tid: [] for tid in team_ids}
    for pl in players:
        tid = pl.current_team_id
        if tid is None or tid not in by_team:
            continue
        by_team[tid].append(pl)

    fwd_scores: list[tuple[Team, float]] = []
    def_scores: list[tuple[Team, float]] = []
    g_scores: list[tuple[Team, float]] = []
    top_player_by_tid: dict[int, tuple[Player, int]] = {}

    for tm in teams:
        plist = by_team.get(tm.id, [])
        fwd_scores.append((tm, _avg_top_n_ovrs(plist, _is_forward, 12)))
        def_scores.append((tm, _avg_top_n_ovrs(plist, _is_defense, 6)))
        g_scores.append((tm, _avg_top_n_ovrs(plist, _is_goalie, 2)))
        best_pl: Player | None = None
        best_ovr = -1
        for pl in plist:
            o = _player_ovr(pl)
            if o is None:
                continue
            if int(o) > best_ovr:
                best_ovr = int(o)
                best_pl = pl
        if best_pl is not None:
            top_player_by_tid[tm.id] = (best_pl, best_ovr)

    def _rank_map(scored: list[tuple[Team, float]]) -> dict[int, int]:
        ordered = sorted(scored, key=lambda x: (-x[1], (x[0].name or "").lower(), x[0].id))
        return {tm.id: idx for idx, (tm, _) in enumerate(ordered, start=1)}

    f_rank = _rank_map(fwd_scores)
    d_rank = _rank_map(def_scores)
    g_rank = _rank_map(g_scores)

    combined: list[tuple[Team, int, int, int]] = []
    for tm in teams:
        combined.append((tm, f_rank[tm.id], d_rank[tm.id], g_rank[tm.id]))
    combined.sort(key=lambda x: (x[1] + x[2] + x[3], (x[0].name or "").lower(), x[0].id))

    n_teams = len(combined)
    out: list[dict[str, Any]] = []
    for idx, (tm, fr, dr, gr) in enumerate(combined, start=1):
        if n_teams > 1:
            talent = max(0.5, 5.0 - (idx - 1) * (3.5 / (n_teams - 1)))
        else:
            talent = 5.0
        tp = top_player_by_tid.get(tm.id)
        top_pl = tp[0] if tp else None
        pos_l = ""
        if top_pl:
            pos_l = player_positions_display_label(top_pl)
            if pos_l and pos_l != "—" and " • " in pos_l:
                pos_l = pos_l.split(" • ")[0].strip()
            elif not pos_l or pos_l == "—":
                pos_l = ((top_pl.position or "") or "").strip().upper() or "?"
        out.append(
            {
                "rank": idx,
                "team": tm,
                "f_rank": fr,
                "d_rank": dr,
                "g_rank": gr,
                "top_player": top_pl,
                "top_pos_label": pos_l,
                "talent_stars": round(talent * 2) / 2.0,
            }
        )
    return out


def load_latest_positional_rank_snapshot(league_slug: str) -> tuple[dict[int, int], datetime | None]:
    slug = (league_slug or "").strip()
    if not slug:
        return {}, None
    row = (
        db.session.query(PositionalRankSnapshot)
        .filter(PositionalRankSnapshot.league_slug == slug)
        .order_by(PositionalRankSnapshot.snapshot_at.desc())
        .first()
    )
    if not row:
        return {}, None
    try:
        raw = json.loads(row.ranks_json or "{}")
    except json.JSONDecodeError:
        return {}, row.snapshot_at
    out: dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out, row.snapshot_at


def save_positional_rank_snapshot(league_slug: str, rows: list[dict[str, Any]]) -> None:
    ranks = {str(int(r["team"].id)): int(r["rank"]) for r in rows}
    snap = PositionalRankSnapshot(
        league_slug=(league_slug or "").strip(),
        snapshot_at=datetime.utcnow(),
        ranks_json=json.dumps(ranks, sort_keys=True),
    )
    db.session.add(snap)
    db.session.commit()


def apply_positional_rank_trends(rows: list[dict[str, Any]], prev_rank_by_team: dict[int, int]) -> None:
    """Same semantics as prospect system ranks: mutates trend_dir / trend_delta on each row."""
    apply_system_rank_trends(rows, prev_rank_by_team)


def record_positional_rank_snapshot_after_import(app: object) -> None:
    with app.app_context():
        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        rows = build_positional_ranking_rows(db.session)
        if not rows:
            return
        save_positional_rank_snapshot(slug, rows)
