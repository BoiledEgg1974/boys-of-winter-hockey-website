"""Line-context advanced stats from FHM team_lines.csv and player process metrics."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Config
from app.models import Player, PlayerSkaterStat, Team
from app.services.advanced_stats import _pct_from_pair, _player_pts_per_60
from scripts.import_pipeline.encoding_utils import normalize_header, to_int

ES_FORWARD_UNITS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ES L1", ("es_l1_lw", "es_l1_c", "es_l1_rw")),
    ("ES L2", ("es_l2_lw", "es_l2_c", "es_l2_rw")),
    ("ES L3", ("es_l3_lw", "es_l3_c", "es_l3_rw")),
    ("ES L4", ("es_l4_lw", "es_l4_c", "es_l4_rw")),
)
ES_DEFENSE_UNITS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ES L1", ("es_l1_ld", "es_l1_rd")),
    ("ES L2", ("es_l2_ld", "es_l2_rd")),
    ("ES L3", ("es_l3_ld", "es_l3_rd")),
    ("ES L4", ("es_l4_ld", "es_l4_rd")),
)

LINE_TYPE_FORWARD = "forward"
LINE_TYPE_DEFENSE = "defense"
LINE_TYPE_ALL = "all"


def _raw_import_dir(raw_import_dir: Path | None = None) -> Path:
    if raw_import_dir is not None:
        return raw_import_dir
    return Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))


def _load_current_team_lines(raw_dir: Path) -> dict[int, dict[str, Any]]:
    path = raw_dir / "team_lines.csv"
    if not path.is_file():
        return {}
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                rows = list(reader)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {}

    by_team: dict[int, dict[str, Any]] = {}
    for row in rows:
        normalized = {normalize_header(key): (value or "").strip() for key, value in row.items() if key is not None}
        team_id = to_int(normalized.get("teamid"))
        if team_id is None:
            continue
        assignments = {
            key: value
            for key, value in normalized.items()
            if value
            and key.startswith(("es_", "pp", "pk", "4on4_", "3on3_", "shootout_", "goalie_", "extra_attacker_"))
        }
        by_team[int(team_id)] = {"assignments": assignments, "raw": normalized}
    return by_team


def _shot_share_proxy(sf_per_60: float | None, sa_per_60: float | None) -> float | None:
    if sf_per_60 is None or sa_per_60 is None:
        return None
    total = float(sf_per_60) + float(sa_per_60)
    if total <= 0:
        return None
    return round(100.0 * float(sf_per_60) / total, 1)


def _player_process_snapshot(st: PlayerSkaterStat | None) -> dict[str, Any]:
    if st is None:
        return {
            "gp": 0,
            "toi_seconds": 0,
            "cf_pct": None,
            "ff_pct": None,
            "sf_per_60": None,
            "pts_per_60": None,
            "pdo": None,
            "gf_per_60": None,
            "ga_per_60": None,
            "shot_share_proxy": None,
        }
    return {
        "gp": int(st.gp or 0),
        "toi_seconds": int(st.toi_seconds or 0),
        "cf_pct": st.cf_pct if st.cf_pct is not None else _pct_from_pair(st.cf, st.ca),
        "ff_pct": st.ff_pct if st.ff_pct is not None else _pct_from_pair(st.ff, st.fa),
        "sf_per_60": st.sf_per_60,
        "pts_per_60": _player_pts_per_60(st),
        "pdo": st.pdo,
        "gf_per_60": st.gf_per_60,
        "ga_per_60": st.ga_per_60,
        "shot_share_proxy": _shot_share_proxy(st.sf_per_60, st.sa_per_60),
    }


def _weighted_or_simple_avg(
    values: list[tuple[float | None, float | None]],
    *,
    decimals: int = 1,
) -> float | None:
    pairs = [(float(v), float(w)) for v, w in values if v is not None]
    if not pairs:
        return None
    weights = [w for _, w in pairs if w > 0]
    if len(weights) == len(pairs) and sum(weights) > 0:
        total_w = sum(weights)
        return round(sum(v * w for v, w in pairs if w > 0) / total_w, decimals)
    return round(sum(v for v, _ in pairs) / len(pairs), decimals)


def aggregate_line_metrics(player_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate player-level process metrics for a line combination."""
    combined_gp = sum(int(s.get("gp") or 0) for s in player_snapshots)
    combined_toi_seconds = sum(int(s.get("toi_seconds") or 0) for s in player_snapshots)
    missing_stats = any(s.get("cf_pct") is None and s.get("sf_per_60") is None for s in player_snapshots)

    def _avg(key: str, *, decimals: int = 1) -> float | None:
        return _weighted_or_simple_avg(
            [(s.get(key), s.get("toi_seconds")) for s in player_snapshots],
            decimals=decimals,
        )

    return {
        "combined_gp": combined_gp,
        "combined_toi_seconds": combined_toi_seconds,
        "avg_cf_pct": _avg("cf_pct"),
        "avg_ff_pct": _avg("ff_pct"),
        "avg_sf_per_60": _avg("sf_per_60", decimals=2),
        "avg_pts_per_60": _avg("pts_per_60", decimals=2),
        "avg_pdo": _avg("pdo", decimals=1),
        "avg_gf_per_60": _avg("gf_per_60", decimals=2),
        "avg_ga_per_60": _avg("ga_per_60", decimals=2),
        "shot_share_proxy": _avg("shot_share_proxy"),
        "missing_stats": missing_stats,
    }


def _line_specs(line_type: str) -> list[tuple[str, str, tuple[str, ...]]]:
    normalized = (line_type or LINE_TYPE_ALL).strip().lower()
    specs: list[tuple[str, str, tuple[str, ...]]] = []
    if normalized in (LINE_TYPE_FORWARD, LINE_TYPE_ALL):
        for unit, slots in ES_FORWARD_UNITS:
            specs.append(("Forward", unit, slots))
    if normalized in (LINE_TYPE_DEFENSE, LINE_TYPE_ALL):
        for unit, slots in ES_DEFENSE_UNITS:
            specs.append(("Defense Pair", unit, slots))
    return specs


def _build_line_row(
    *,
    team: Team,
    line_type_label: str,
    unit: str,
    slot_keys: tuple[str, ...],
    assignments: dict[str, str],
    players_by_fhm: dict[str, Player],
    stats_by_player_id: dict[int, PlayerSkaterStat],
) -> dict[str, Any] | None:
    players: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for slot in slot_keys:
        fhm_id = (assignments.get(slot) or "").strip()
        if not fhm_id:
            return None
        player = players_by_fhm.get(fhm_id)
        if player is None:
            return None
        stat = stats_by_player_id.get(int(player.id))
        snap = _player_process_snapshot(stat)
        players.append(
            {
                "player_id": int(player.id),
                "player_name": player.full_name,
                "fhm_player_id": fhm_id,
                "slot": slot,
            }
        )
        snapshots.append(snap)

    metrics = aggregate_line_metrics(snapshots)
    return {
        "team": team,
        "line_type": line_type_label,
        "unit": unit,
        "players": players,
        "players_label": " · ".join(p["player_name"] for p in players),
        "player_count": len(players),
        **metrics,
    }


def build_line_stats_rows(
    session: Session,
    season_id: int,
    *,
    segment: str = "rs",
    team_id: int | None = None,
    line_type: str = LINE_TYPE_ALL,
    min_combined_gp: int = 0,
    min_combined_toi_seconds: int = 0,
    raw_import_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build line-context rows from current FHM line combinations and player process stats."""
    raw_dir = _raw_import_dir(raw_import_dir)
    team_lines = _load_current_team_lines(raw_dir)

    team_query = select(Team).where(Team.fhm_team_id.isnot(None)).order_by(Team.name)
    if team_id is not None:
        team_query = team_query.where(Team.id == team_id)
    teams = session.scalars(team_query).all()

    players_by_fhm = {
        str(p.fhm_player_id).strip(): p
        for p in session.scalars(select(Player).where(Player.fhm_player_id.isnot(None))).all()
        if str(p.fhm_player_id).strip()
    }

    stat_rows = session.scalars(
        select(PlayerSkaterStat)
        .options(joinedload(PlayerSkaterStat.player))
        .where(
            PlayerSkaterStat.season_id == season_id,
            PlayerSkaterStat.stat_segment == segment,
        )
    ).all()
    stats_by_player_id = {int(st.player_id): st for st in stat_rows}

    out: list[dict[str, Any]] = []
    for team in teams:
        fhm_team_id = team.fhm_team_id
        if fhm_team_id is None:
            continue
        line_entry = team_lines.get(int(fhm_team_id))
        if not line_entry:
            continue
        assignments = line_entry.get("assignments") or {}
        for line_type_label, unit, slots in _line_specs(line_type):
            row = _build_line_row(
                team=team,
                line_type_label=line_type_label,
                unit=unit,
                slot_keys=slots,
                assignments=assignments,
                players_by_fhm=players_by_fhm,
                stats_by_player_id=stats_by_player_id,
            )
            if row is None:
                continue
            if row["combined_gp"] < min_combined_gp:
                continue
            if row["combined_toi_seconds"] < min_combined_toi_seconds:
                continue
            out.append(row)

    out.sort(
        key=lambda r: (
            (r.get("team").name if r.get("team") else ""),
            0 if r.get("line_type") == "Forward" else 1,
            r.get("unit") or "",
        )
    )
    return out


def line_stats_filter_options(session: Session) -> dict[str, Any]:
    teams = session.scalars(select(Team).where(Team.fhm_team_id.isnot(None)).order_by(Team.name)).all()
    return {
        "teams": teams,
        "line_types": (
            {"key": LINE_TYPE_ALL, "label": "All lines"},
            {"key": LINE_TYPE_FORWARD, "label": "Forward lines"},
            {"key": LINE_TYPE_DEFENSE, "label": "Defense pairs"},
        ),
        "segments": (
            {"key": "rs", "label": "Regular season"},
            {"key": "ps", "label": "Preseason"},
            {"key": "po", "label": "Playoffs"},
        ),
    }


def build_line_stats_json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        team = row.get("team")
        out.append(
            {
                "team": {
                    "id": int(team.id),
                    "name": team.name,
                    "abbreviation": team.abbreviation,
                    "slug": team.slug,
                }
                if team is not None
                else None,
                "line_type": row.get("line_type"),
                "unit": row.get("unit"),
                "players": row.get("players"),
                "players_label": row.get("players_label"),
                "player_count": row.get("player_count"),
                "combined_gp": row.get("combined_gp"),
                "combined_toi_seconds": row.get("combined_toi_seconds"),
                "avg_cf_pct": row.get("avg_cf_pct"),
                "avg_ff_pct": row.get("avg_ff_pct"),
                "avg_sf_per_60": row.get("avg_sf_per_60"),
                "avg_pts_per_60": row.get("avg_pts_per_60"),
                "avg_pdo": row.get("avg_pdo"),
                "avg_gf_per_60": row.get("avg_gf_per_60"),
                "avg_ga_per_60": row.get("avg_ga_per_60"),
                "shot_share_proxy": row.get("shot_share_proxy"),
                "missing_stats": row.get("missing_stats"),
            }
        )
    return out
