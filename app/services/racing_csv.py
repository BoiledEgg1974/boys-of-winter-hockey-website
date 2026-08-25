"""Helpers shared by Formula / Demolition racing mounts."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable


def normalize_name_key(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_export_stamp(filename: str) -> str | None:
    """Extract trailing datetime stamp from ``kind_YYYY-MM-DD_HH-MM-SS.csv``."""
    stem = Path(filename).stem
    m = re.search(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$", stem)
    return m.group(1) if m else None


def classify_export_filename(filename: str) -> str | None:
    name = Path(filename).name.lower()
    # Longer / Godot EXPORT CSV prefixes first so they do not collide with shorter names.
    mapping = (
        ("race_results_", "race_results"),
        ("qualifying_standings_", "qualifying_standings"),
        ("circuit_standings_", "circuit_standings"),
        ("race_channel_points_", "race_channel_points"),
        ("channel_points_", "channel_points"),
        ("viewer_finish_awards_", "viewer_finish_awards"),
        ("viewer_race_ap_", "viewer_finish_awards"),
        ("viewer_credit_ledger_", "viewer_credit_ledger"),
        ("viewer_ap_ledger_", "viewer_credit_ledger"),
        ("event_results_", "event_results"),
        ("kill_awards_", "kill_awards"),
        ("season_standings_", "season_standings"),
        ("circuit_ap_awards_", "circuit_ap_awards"),
        ("viewer_circuit_ap_", "circuit_ap_awards"),
        ("channel_credits_", "channel_credits"),
        ("viewer_credits_", "viewer_credits"),
    )
    for prefix, kind in mapping:
        if name.startswith(prefix) and name.endswith(".csv"):
            return kind
    return None


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows: list[dict[str, str]] = []
        for row in reader:
            if not row:
                continue
            rows.append({(k or "").strip(): (v or "").strip() if v is not None else "" for k, v in row.items()})
        return rows


def cell_int(row: dict[str, str], *keys: str, default: int = 0) -> int:
    for key in keys:
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        try:
            return int(float(str(raw).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return default


def cell_float(row: dict[str, str], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return default


def cell_bool(row: dict[str, str], *keys: str) -> bool | None:
    for key in keys:
        raw = (row.get(key) or "").strip().lower()
        if raw in ("1", "true", "yes", "y"):
            return True
        if raw in ("0", "false", "no", "n"):
            return False
    return None


def formula_circuit_points_for_position(position: int) -> int:
    """Official Formula BOWL P1–P10 circuit points (classified order, including DNFs)."""
    table = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
    return int(table.get(int(position), 0))


FORMULA_CIRCUIT_AP_FIRST = 1000
FORMULA_CIRCUIT_AP_LAST = 10
FORMULA_CIRCUIT_AP_PLACES = 31
FORMULA_CIRCUIT_CP_FIRST_PLACE = 11
FORMULA_CIRCUIT_CP_LAST_PLACE = 31
FORMULA_CIRCUIT_CP_FIRST = 3000
FORMULA_CIRCUIT_CP_LAST = 300


def formula_circuit_ap_for_rank(rank: int, field_size: int | None = None) -> int:
    """End-of-circuit AP: 1st = 1000, 31st = 10, linear in between.

    Always a 31-place table. ``field_size`` is ignored so a larger field cannot
    stretch 10 AP past 31st, and a short field still projects rank 31 at 10 AP.
    """
    n = FORMULA_CIRCUIT_AP_PLACES
    place = int(rank)
    if place < 1 or place > n:
        return 0
    if n == 1:
        return FORMULA_CIRCUIT_AP_FIRST
    t = float(place - 1) / float(n - 1)
    return int(round(FORMULA_CIRCUIT_AP_FIRST - t * (FORMULA_CIRCUIT_AP_FIRST - FORMULA_CIRCUIT_AP_LAST)))


def formula_circuit_channel_points_for_rank(rank: int) -> int:
    """End-of-circuit Twitch credits: P11 = 3000, P31 = 300, linear. P1–P10 = 0."""
    first = FORMULA_CIRCUIT_CP_FIRST_PLACE
    last = FORMULA_CIRCUIT_CP_LAST_PLACE
    place = int(rank)
    if place < first or place > last:
        return 0
    span = last - first
    if span <= 0:
        return FORMULA_CIRCUIT_CP_FIRST
    t = float(place - first) / float(span)
    return int(round(FORMULA_CIRCUIT_CP_FIRST - t * (FORMULA_CIRCUIT_CP_FIRST - FORMULA_CIRCUIT_CP_LAST)))


def derby_event_ap_for_position(position: int) -> int:
    """Top six of each derby night: 6 / 5 / 4 / 3 / 2 / 1."""
    table = {1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1}
    return int(table.get(int(position), 0))


def derby_circuit_ap_for_rank(rank: int) -> int:
    """End-of-circuit AP: 30 / 25 / 20 / 15 / 10 / 5."""
    table = {1: 30, 2: 25, 3: 20, 4: 15, 5: 10, 6: 5}
    return int(table.get(int(rank), 0))


def list_export_csvs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(
        [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv"],
        key=lambda p: p.name.lower(),
    )


def _export_recency_key(filename: str) -> tuple[str, str]:
    stamp = parse_export_stamp(filename) or ""
    return (stamp, Path(filename).name.lower())


def select_latest_export_csvs(raw_dir: Path) -> list[Path]:
    """Keep only the newest stamped file of each export kind.

    Scanning the whole raw folder used to re-apply leftover sample CSVs (Alice/Bob/Carol)
    after a real Godot EXPORT, which put placeholder drivers back on the homepage.
    """
    latest: dict[str, Path] = {}
    for path in list_export_csvs(raw_dir):
        kind = classify_export_filename(path.name)
        if kind is None:
            continue
        current = latest.get(kind)
        if current is None or _export_recency_key(path.name) > _export_recency_key(current.name):
            latest[kind] = path
    return list(latest.values())


def group_rows_by(rows: Iterable[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        k = str(row.get(key) or "").strip() or "_"
        out.setdefault(k, []).append(row)
    return out


def as_payload_rows(rows: list[Any], limit: int = 20) -> list[dict[str, Any]]:
    return list(rows[:limit])
