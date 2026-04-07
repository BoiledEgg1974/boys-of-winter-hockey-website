"""Infer primary position from ``player_ratings.csv`` and ``team_lines.csv`` for NULL ``Player.position`` rows."""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import and_

from app.config import Config
from app.models import Player

# Mirrors scripts.import_pipeline.fhm_loader.import_ratings position columns.
_POS_COLS: tuple[tuple[str, str], ...] = (
    ("G", "g"),
    ("D", "ld"),
    ("D", "rd"),
    ("LW", "lw"),
    ("C", "c"),
    ("RW", "rw"),
)

_bucket_sets: dict[str, set[str]] | None = None


def _team_lines_header_bucket(header: str) -> str | None:
    """Map a ``team_lines.csv`` column name to lw / c / rw / d / g / fwd."""
    h = header.strip().upper()
    if not h or h == "TEAMID":
        return None
    if "GOALIE" in h:
        return "g"
    if h.endswith(" LW"):
        return "lw"
    if h.endswith(" RW"):
        return "rw"
    if h.endswith(" LD") or h.endswith(" RD"):
        return "d"
    if h.endswith(" C"):
        return "c"
    if " F1" in h or " F2" in h or " F3" in h:
        return "fwd"
    if "EXTRA ATTACKER" in h:
        return "fwd"
    return None


def _load_team_lines_bucket_sets() -> dict[str, set[str]]:
    """Player FHM ids seen in current roster line slots (LW/C/RW/LD/RD/Goalie/F lines)."""
    out: dict[str, set[str]] = {
        "lw": set(),
        "c": set(),
        "rw": set(),
        "d": set(),
        "g": set(),
        "fwd": set(),
    }
    path = Path(Config.RAW_IMPORT_DIR) / "team_lines.csv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        rows = list(reader)
    if not rows:
        return out
    headers = rows[0]
    buckets = [_team_lines_header_bucket(h) for h in headers]
    for row in rows[1:]:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        for i, b in enumerate(buckets):
            if b is None or i >= len(row):
                continue
            cell = str(row[i]).strip() if row[i] is not None else ""
            if not cell:
                continue
            try:
                pid_s = str(int(float(cell)))
            except (TypeError, ValueError):
                continue
            if b == "fwd":
                out["fwd"].add(pid_s)
            else:
                out[b].add(pid_s)
                if b in ("lw", "c", "rw"):
                    out["fwd"].add(pid_s)
    return out


def _norm_key(k: str) -> str:
    return str(k).strip().lower().replace(" ", "_")


def _to_int(val) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).strip()))
    except (TypeError, ValueError):
        return None


def _load_ratings_only_bucket_sets() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {
        "lw": set(),
        "c": set(),
        "rw": set(),
        "d": set(),
        "g": set(),
        "fwd": set(),
    }
    path = Path(Config.RAW_IMPORT_DIR) / "player_ratings.csv"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if not reader.fieldnames:
            return out
        fh = {_norm_key(k): k for k in reader.fieldnames}
        if not fh.get("playerid"):
            return out
        for row in reader:
            raw = {_norm_key(k): v for k, v in row.items()}
            pid = raw.get("playerid")
            if pid is None or str(pid).strip() == "":
                continue
            pid_s = str(int(float(str(pid).strip()))) if str(pid).strip() else ""
            if not pid_s:
                continue
            best = None
            best_v = -1
            for pos, col in _POS_COLS:
                nk = _norm_key(col)
                v = _to_int(raw.get(nk))
                if v is not None and v > best_v:
                    best_v = v
                    best = pos
            if not best:
                continue
            if best == "G":
                out["g"].add(pid_s)
            elif best == "D":
                out["d"].add(pid_s)
            elif best == "LW":
                out["lw"].add(pid_s)
                out["fwd"].add(pid_s)
            elif best == "C":
                out["c"].add(pid_s)
                out["fwd"].add(pid_s)
            elif best == "RW":
                out["rw"].add(pid_s)
                out["fwd"].add(pid_s)
    return out


def _load_bucket_sets() -> dict[str, set[str]]:
    global _bucket_sets
    if _bucket_sets is not None:
        return _bucket_sets
    ratings = _load_ratings_only_bucket_sets()
    lines = _load_team_lines_bucket_sets()
    _bucket_sets = {k: ratings[k] | lines[k] for k in ratings}
    return _bucket_sets


def fhm_fallback_for_bucket(bucket: str):
    """Match players with NULL DB position but inferred from ratings and/or team line slots."""
    b = bucket.lower()
    sets = _load_bucket_sets()
    ids = sets.get(b)
    if not ids:
        return None
    return and_(Player.position.is_(None), Player.fhm_player_id.in_(ids))


def reset_ratings_position_cache() -> None:
    """Tests / re-import hooks."""
    global _bucket_sets
    _bucket_sets = None


def backfill_null_positions_from_ratings(session) -> int:
    """
    Set ``Player.position`` when it is NULL using ``player_ratings.csv`` (same rules as FHM import).
    Run after imports or when retirees are missing from position filters. Returns update count.
    """
    from sqlalchemy import select

    path = Path(Config.RAW_IMPORT_DIR) / "player_ratings.csv"
    if not path.is_file():
        return 0
    n = 0
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if not reader.fieldnames:
            return 0
        fh = {_norm_key(k): k for k in reader.fieldnames}
        pid_key = fh.get("playerid")
        if not pid_key:
            return 0
        for row in reader:
            raw = {_norm_key(k): v for k, v in row.items()}
            pid = raw.get("playerid")
            if pid is None or str(pid).strip() == "":
                continue
            pid_s = str(int(float(str(pid).strip())))
            best = None
            best_v = -1
            for pos, col in _POS_COLS:
                nk = _norm_key(col)
                v = _to_int(raw.get(nk))
                if v is not None and v > best_v:
                    best_v = v
                    best = pos
            if not best:
                continue
            pl = session.scalars(
                select(Player).where(Player.fhm_player_id == pid_s, Player.position.is_(None)).limit(1)
            ).first()
            if pl:
                pl.position = best
                n += 1
    if n:
        session.commit()
    reset_ratings_position_cache()
    return n
