"""Team staff from FHM ``staff_master.csv`` + ``staff_ratings.csv`` (per-league RAW_IMPORT_DIR)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from charset_normalizer import from_path
from flask import current_app, has_app_context

from app.config import Config
from scripts.import_pipeline.encoding_utils import (
    cell_val,
    detect_delimiter,
    normalize_header,
    read_csv_normalized,
    to_int,
)

# (csv_key, (line1, line2)) — line2 may be "" for a single-line header cell.
STAFF_COACH_COLUMNS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("coaching_g", ("Coaching", "G")),
    ("coaching_defense", ("Coaching", "defense")),
    ("coaching_forwards", ("Coaching", "forwards")),
    ("coaching_prospects", ("Coaching", "prospects")),
    ("def_skills", ("Def", "skills")),
    ("off_skills", ("Off", "skills")),
    ("phy_training", ("Phy", "training")),
    ("player_management", ("Player", "mgmt")),
    ("motivation", ("Motiv.", "")),
    ("discipline", ("Disc.", "")),
    ("negotiating", ("Negot.", "")),
    ("tactics", ("Tactics", "")),
    ("ingame_tactics", ("In-game", "tactics")),
)

STAFF_SCOUT_COLUMNS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("evaluate_abilities", ("Evaluate", "ability")),
    ("evaluate_potential", ("Evaluate", "potential")),
    ("motivation", ("Motiv.", "")),
    ("discipline", ("Disc.", "")),
)

STAFF_TRAINER_COLUMNS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("trainer_skill", ("Trainer", "skill")),
    ("phy_training", ("Phy", "training")),
    ("coaching_prospects", ("Prospect", "dev.")),
    ("def_skills", ("Def", "skills")),
    ("off_skills", ("Off", "skills")),
)


def _all_staff_attr_keys() -> frozenset[str]:
    return frozenset(
        k
        for cols in (STAFF_COACH_COLUMNS, STAFF_SCOUT_COLUMNS, STAFF_TRAINER_COLUMNS)
        for k, _ in cols
    )


_bundle_by_team: dict[str, dict[str, list[dict[str, object]]]] | None = None
_bundle_key: tuple[float, float] | None = None


def _fix_evaluate_columns(d: dict[str, str], row: list[str], header: list[str]) -> None:
    """When FHM writes ``;;`` before the scout eval numbers, Abilities is empty and a 31st field holds Potential."""
    n_h = len(header)
    extra = row[n_h:] if len(row) > n_h else []
    ab = (d.get("evaluate_abilities") or "").strip()
    if ab:
        return
    if not extra:
        return
    pot_in_dict = (d.get("evaluate_potential") or "").strip()
    if not pot_in_dict:
        return
    d["evaluate_abilities"] = pot_in_dict
    d["evaluate_potential"] = extra[0].strip()


def _read_staff_ratings_by_id(path: Path) -> dict[str, dict[str, str]]:
    """Parse ``staff_ratings.csv`` with csv (handles ``;;`` and 31 fields vs 30 headers). Last row per StaffId wins."""
    raw = path.read_bytes()
    result = from_path(str(path)).best()
    encoding = result.encoding if result else "utf-8"
    try:
        text = raw.decode(encoding)
    except Exception:
        text = raw.decode("utf-8", errors="replace")
    delimiter = detect_delimiter(text[:8192] if len(text) > 8192 else text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        raw_header = next(reader)
    except StopIteration:
        return {}
    header = [normalize_header(c) for c in raw_header]
    n_h = len(header)
    out: dict[str, dict[str, str]] = {}
    for row in reader:
        if not row or not any(str(x).strip() for x in row):
            continue
        if len(row) < n_h:
            row = list(row) + [""] * (n_h - len(row))
        d = dict(zip(header, row[:n_h]))
        _fix_evaluate_columns(d, row, header)
        sid = cell_val(d, "staffid")
        if sid:
            out[str(sid).strip()] = d
    return out


def _float_attr(rr: dict | None, key: str) -> float | None:
    if not rr:
        return None
    raw = cell_val(rr, key)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _staff_role_bucket(rr: dict | None) -> str:
    """Primary staff role from FHM Coach / Scout / Trainer (game's hired-role proxy).

    Whoever has the highest aptitude in those three is treated as that role. When two tie for the
    maximum, order is coaches → scouts → trainers (typical bench vs scouting vs medical split).
    """
    if not rr:
        return "coaches"
    co = to_int(cell_val(rr, "coach"), 0) or 0
    sc = to_int(cell_val(rr, "scout"), 0) or 0
    tr = to_int(cell_val(rr, "trainer"), 0) or 0
    best = max(co, sc, tr)
    if best <= 0:
        return "coaches"
    for val, bucket in ((co, "coaches"), (sc, "scouts"), (tr, "trainers")):
        if val == best:
            return bucket
    return "coaches"


def _load_bundle() -> dict[str, dict[str, list[dict[str, object]]]]:
    global _bundle_by_team, _bundle_key
    raw_dir = (
        Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
        if has_app_context()
        else Path(Config.RAW_IMPORT_DIR)
    )
    mp = raw_dir / "staff_master.csv"
    rp = raw_dir / "staff_ratings.csv"
    if not mp.is_file() or not rp.is_file():
        _bundle_by_team = {}
        _bundle_key = (0.0, 0.0)
        return _bundle_by_team

    key = (mp.stat().st_mtime, rp.stat().st_mtime)
    if _bundle_by_team is not None and _bundle_key == key:
        return _bundle_by_team

    ratings_by_id = _read_staff_ratings_by_id(rp)

    by_team: dict[str, dict[str, list[dict[str, object]]]] = {}
    master_df = read_csv_normalized(mp)
    for _, mrow in master_df.iterrows():
        m = mrow.to_dict()
        if to_int(cell_val(m, "retired"), 0) == 1:
            continue
        tid_raw = cell_val(m, "teamid")
        if tid_raw is None or str(tid_raw).strip() in ("", "-1"):
            continue
        tid = str(tid_raw).strip()
        sid = cell_val(m, "staffid")
        if not sid:
            continue
        rr = ratings_by_id.get(str(sid).strip())
        fn = (cell_val(m, "first_name") or "").strip()
        ln = (cell_val(m, "last_name") or "").strip()
        full = f"{fn} {ln}".strip() or "—"
        nick = (cell_val(m, "nick_name") or "").strip()
        if nick:
            full = f'{full} “{nick}”'
        nat = (cell_val(m, "nationality_one") or "").strip() or "—"
        bucket = _staff_role_bucket(rr)
        attrs: dict[str, float | None] = {}
        for k in _all_staff_attr_keys():
            attrs[k] = _float_attr(rr, k)
        entry: dict[str, object] = {
            "full_name": full,
            "nationality": nat,
            "attrs": attrs,
            "_sort": ((ln or full).lower(), (fn or "").lower()),
        }
        team_entry = by_team.setdefault(
            tid, {"coaches": [], "scouts": [], "trainers": []}
        )
        team_entry[bucket].append(entry)

    for _, buckets in by_team.items():
        for bname in ("coaches", "scouts", "trainers"):
            buckets[bname].sort(key=lambda e: e["_sort"])  # type: ignore[arg-type, return-value]
            for e in buckets[bname]:
                del e["_sort"]

    _bundle_by_team = by_team
    _bundle_key = key
    return _bundle_by_team


def get_staff_sections_for_team(
    fhm_team_id: str | int | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (coaches, scouts, trainers) row dicts for the given FHM team id."""
    if fhm_team_id is None or str(fhm_team_id).strip() == "":
        return [], [], []
    tid = str(fhm_team_id).strip()
    bundle = _load_bundle()
    block = bundle.get(tid, {"coaches": [], "scouts": [], "trainers": []})
    return (
        list(block["coaches"]),
        list(block["scouts"]),
        list(block["trainers"]),
    )
