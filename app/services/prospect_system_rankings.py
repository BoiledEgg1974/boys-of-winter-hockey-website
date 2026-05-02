"""League-wide prospect system rankings (farm strength) + snapshot baselines for trend arrows."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import Config
from app.models import Player, PlayerGoalieStat, PlayerSkaterStat, Team, db
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.seasons import get_current_season
from app.site_models import ProspectSystemRankSnapshot


def _age_years(birth: date | None, as_of: date | None) -> int | None:
    if birth is None:
        return None
    ref = as_of if as_of is not None else date.today()
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


def _prospect_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def resolve_prospect_team_fallbacks(session: object, players: list[Player], season: object | None) -> dict[int, Team | None]:
    """Same fallbacks as the Prospects page: GP-based team, then player_rights.csv."""
    resolved_team_by_player_id: dict[int, Team | None] = {}
    missing_ids = [p.id for p in players if p.current_team is None]
    if missing_ids and season:
        inferred_team_id: dict[int, tuple[int, int]] = {}
        sk_rows = session.execute(
            select(
                PlayerSkaterStat.player_id,
                PlayerSkaterStat.team_id,
                PlayerSkaterStat.gp,
            ).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.player_id.in_(missing_ids),
                PlayerSkaterStat.team_id.isnot(None),
            )
        ).all()
        for pid, tid, gp in sk_rows:
            if tid is None:
                continue
            gpv = int(gp or 0)
            prev = inferred_team_id.get(int(pid))
            if prev is None or gpv > prev[0]:
                inferred_team_id[int(pid)] = (gpv, int(tid))
        goalie_rows = session.execute(
            select(
                PlayerGoalieStat.player_id,
                PlayerGoalieStat.team_id,
                PlayerGoalieStat.gp,
            ).where(
                PlayerGoalieStat.season_id == season.id,
                PlayerGoalieStat.player_id.in_(missing_ids),
                PlayerGoalieStat.team_id.isnot(None),
            )
        ).all()
        for pid, tid, gp in goalie_rows:
            if tid is None:
                continue
            gpv = int(gp or 0)
            prev = inferred_team_id.get(int(pid))
            if prev is None or gpv > prev[0]:
                inferred_team_id[int(pid)] = (gpv, int(tid))
        team_ids = sorted({v[1] for v in inferred_team_id.values()})
        teams_map = {
            t.id: t
            for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        } if team_ids else {}
        for pid, (_gp, tid) in inferred_team_id.items():
            resolved_team_by_player_id[pid] = teams_map.get(tid)

    unresolved_ids = [p.id for p in players if p.current_team is None and p.id not in resolved_team_by_player_id]
    if unresolved_ids:
        try:
            rights_path = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "player_rights.csv"
            if rights_path.is_file():
                unresolved_fhm_to_player_id: dict[str, int] = {}
                unresolved_id_set = set(unresolved_ids)
                unresolved_db_only_ids: set[int] = set()
                for p in players:
                    if p.id not in unresolved_id_set:
                        continue
                    if p.fhm_player_id is None:
                        unresolved_db_only_ids.add(p.id)
                    else:
                        fhm_pid = str(p.fhm_player_id).strip()
                        if fhm_pid:
                            unresolved_fhm_to_player_id[fhm_pid] = p.id
                        else:
                            unresolved_db_only_ids.add(p.id)

                pid_to_fhm_team: dict[int, str] = {}
                with rights_path.open("r", encoding="utf-8-sig", newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    delim = ";" if sample.count(";") >= sample.count(",") else ","
                    reader = csv.DictReader(f, delimiter=delim)
                    for row in reader:
                        pid_s = (row.get("PlayerId") or row.get("playerid") or "").strip()
                        tid_s = (row.get("Team") or row.get("team") or "").strip()
                        if not pid_s or not tid_s:
                            continue
                        pid = unresolved_fhm_to_player_id.get(pid_s)
                        if pid is None:
                            try:
                                pid_db = int(pid_s)
                            except ValueError:
                                continue
                            if pid_db not in unresolved_db_only_ids:
                                continue
                            pid = pid_db
                        pid_to_fhm_team[pid] = tid_s
                if pid_to_fhm_team:
                    want_fhm_ids = {v for v in pid_to_fhm_team.values() if v}
                    fhm_team_map: dict[str, Team] = {}
                    if want_fhm_ids:
                        t_rows = session.scalars(select(Team).where(Team.fhm_team_id.in_(want_fhm_ids))).all()
                        fhm_team_map = {str(t.fhm_team_id): t for t in t_rows if t.fhm_team_id is not None}
                    for pid, fhm_tid in pid_to_fhm_team.items():
                        tm = fhm_team_map.get(str(fhm_tid))
                        if tm is not None:
                            resolved_team_by_player_id[pid] = tm
        except Exception:
            pass
    return resolved_team_by_player_id


def build_prospect_system_ranking_rows(
    session: object,
    *,
    players: list[Player],
    resolved_team_by_player_id: dict[int, Team | None],
    league_ids: set[int] | frozenset,
    age_ref: object | None,
    overview_headers: tuple[tuple[str, str, str], ...],
    team_by_id: dict[int, Team],
    effective_team: Callable[[Player], Team | None],
) -> list[dict[str, Any]]:
    """Full-league system ranking rows (top five prospects per team; teams by avg global POT rank)."""
    young_sys: list[Player] = []
    for p in players:
        eff_team = effective_team(p)
        if not eff_team or eff_team.fhm_league_id not in league_ids:
            continue
        age = _age_years(p.birth_date, age_ref)
        if age is None or age > 22:
            continue
        young_sys.append(p)

    sys_items: list[dict] = []
    for pl in young_sys:
        rr = get_player_ratings_row(pl.fhm_player_id)
        attrs: dict[str, float | None] = {}
        attrs_display: dict[str, object | None] = {}
        if rr:
            for _full, _abbr, key in overview_headers:
                raw_cell = rr.get(key)
                attrs_display[key] = raw_cell
                attrs[key] = _prospect_float(raw_cell)
        sys_items.append(
            {
                "pl": pl,
                "attrs": attrs,
                "attrs_display": attrs_display,
                "age": _age_years(pl.birth_date, age_ref),
                "rr": rr,
            }
        )

    def _system_pot_sort_key(it: dict) -> tuple:
        pl = it["pl"]
        raw = pl.overall_potential
        v = _prospect_float(raw) if raw is not None else None
        if v is None:
            return (float("-inf"), (pl.full_name or "").lower(), pl.id)
        return (v, (pl.full_name or "").lower(), pl.id)

    sys_items.sort(key=_system_pot_sort_key, reverse=True)

    team_top5: dict[int, list[tuple[int, Player]]] = defaultdict(list)
    for g_rank, it in enumerate(sys_items, start=1):
        pl = it["pl"]
        tm = effective_team(pl)
        if not tm:
            continue
        bucket = team_top5[tm.id]
        if len(bucket) >= 5:
            continue
        bucket.append((g_rank, pl))

    system_tier: list[tuple[float, str, int, list[tuple[int, Player]]]] = []
    for tid, top5 in team_top5.items():
        if not top5:
            continue
        tm = team_by_id.get(tid)
        if tm is None:
            continue
        avg_rank = sum(r for r, _ in top5) / len(top5)
        system_tier.append((avg_rank, (tm.name or "").lower(), tid, top5))
    system_tier.sort(key=lambda row: (row[0], row[1]))

    system_rankings_rows: list[dict[str, Any]] = []
    for idx, (_avg_rank, _nm_low, tid, top5) in enumerate(system_tier, start=1):
        tm = team_by_id[tid]
        slots: list[dict[str, object]] = []
        for g_rank, pl in top5:
            pos_l = player_positions_display_label(pl)
            if pos_l and pos_l != "—" and " • " in pos_l:
                pos_l = pos_l.split(" • ")[0].strip()
            elif not pos_l or pos_l == "—":
                pos_l = ((pl.position or "") or "—").strip().upper() or "—"
            fn = (pl.first_name or "").strip()
            ln = (pl.last_name or "").strip()
            initial = (fn[0].upper() + "." if fn else "?.")
            slots.append(
                {
                    "pos": pos_l,
                    "initial": initial,
                    "last": ln,
                    "global_rank": g_rank,
                    "player": pl,
                }
            )
        system_rankings_rows.append({"rank": idx, "team": tm, "slots": slots})
    return system_rankings_rows


def load_latest_system_rank_snapshot(league_slug: str) -> tuple[dict[int, int], datetime | None]:
    """Return (team_id -> rank from last snapshot, snapshot timestamp). Empty dict if none."""
    slug = (league_slug or "").strip()
    if not slug:
        return {}, None
    row = (
        db.session.query(ProspectSystemRankSnapshot)
        .filter(ProspectSystemRankSnapshot.league_slug == slug)
        .order_by(ProspectSystemRankSnapshot.snapshot_at.desc())
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
            tid = int(k)
            out[tid] = int(v)
        except (TypeError, ValueError):
            continue
    return out, row.snapshot_at


def save_system_rank_snapshot(league_slug: str, rows: list[dict[str, Any]]) -> None:
    ranks = {str(int(r["team"].id)): int(r["rank"]) for r in rows}
    snap = ProspectSystemRankSnapshot(
        league_slug=(league_slug or "").strip(),
        snapshot_at=datetime.utcnow(),
        ranks_json=json.dumps(ranks, sort_keys=True),
    )
    db.session.add(snap)
    db.session.commit()


def apply_system_rank_trends(rows: list[dict[str, Any]], prev_rank_by_team: dict[int, int]) -> None:
    """Mutates each row dict with trend_delta (int), trend_dir ('up'|'down'|'same'|'new'|None)."""
    for row in rows:
        tid = int(row["team"].id)
        cur = int(row["rank"])
        prev = prev_rank_by_team.get(tid)
        if prev is None:
            row["trend_delta"] = None
            row["trend_dir"] = "new"
            continue
        delta = int(prev) - int(cur)
        if delta > 0:
            row["trend_dir"] = "up"
            row["trend_delta"] = delta
        elif delta < 0:
            row["trend_dir"] = "down"
            row["trend_delta"] = -delta
        else:
            row["trend_dir"] = "same"
            row["trend_delta"] = 0


def record_system_rank_snapshot_after_import(app: object) -> None:
    """Recompute system ranks and append a snapshot (call after league data imports)."""
    from app.services.all_time_records import bowl_nhl_league_ids
    from app.services.seasons import season_age_reference_date, season_with_imported_data_fallback

    with app.app_context():
        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        session = db.session
        league_ids = frozenset(bowl_nhl_league_ids(session))
        if not league_ids:
            return
        canonical = get_current_season()
        season_for_stats = season_with_imported_data_fallback(session, canonical) if canonical else None
        age_ref = season_age_reference_date(canonical or season_for_stats)
        overview_headers = (
            ("Skating", "SKT", "skating"),
            ("Shooting", "SHT", "shooting"),
            ("Playmaking", "PLM", "playmaking"),
            ("Defending", "DEF", "defending"),
            ("Physicality", "PHY", "physicality"),
            ("Conditioning", "CON", "conditioning"),
            ("Character", "CHR", "character"),
            ("Hockey sense", "HSN", "hockey_sense"),
        )
        q = select(Player).options(joinedload(Player.current_team)).where(
            Player.retired.is_(False),
            Player.birth_date.isnot(None),
        )
        players = list(session.scalars(q).unique().all())
        resolved = resolve_prospect_team_fallbacks(session, players, season_for_stats)

        def _eff(pl: Player) -> Team | None:
            return pl.current_team or resolved.get(pl.id)

        teams = list(session.scalars(select(Team).where(Team.fhm_league_id.in_(league_ids)).order_by(Team.name)).all())
        team_by_id = {t.id: t for t in teams}
        rows = build_prospect_system_ranking_rows(
            session,
            players=players,
            resolved_team_by_player_id=resolved,
            league_ids=league_ids,
            age_ref=age_ref,
            overview_headers=overview_headers,
            team_by_id=team_by_id,
            effective_team=_eff,
        )
        if not rows:
            return
        save_system_rank_snapshot(slug, rows)
