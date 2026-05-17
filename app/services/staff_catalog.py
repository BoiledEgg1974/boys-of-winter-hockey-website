"""Staff catalog from FHM CSVs for browse, profile, and hire requests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import current_app, has_app_context

from app.config import Config
from app.services.team_staff_csv import (
    STAFF_COACH_COLUMNS,
    STAFF_SCOUT_COLUMNS,
    STAFF_TRAINER_COLUMNS,
    _all_staff_attr_keys,
    _float_attr,
    _read_staff_ratings_by_id,
    _staff_role_bucket,
)
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

STAFF_ROLES: tuple[str, ...] = ("head_coach", "assistant_coach", "scout", "trainer")
BROWSE_FILTERS: tuple[str, ...] = STAFF_ROLES

_ROLE_LABELS: dict[str, str] = {
    "head_coach": "Head Coach",
    "assistant_coach": "Assistant Coach",
    "scout": "Scout",
    "trainer": "Trainer",
}

_MIN_RATING = 16.0

_catalog_by_mtime: dict[str, dict[str, dict[str, Any]]] | None = None
_catalog_key: tuple[float, float] | None = None


def staff_role_label(role: str | None) -> str:
    if not role:
        return "—"
    return _ROLE_LABELS.get(str(role).strip(), str(role).replace("_", " ").title())


def _rating_column_for_browse(filter_key: str) -> str:
    if filter_key in ("head_coach", "assistant_coach"):
        return "coach"
    if filter_key == "scout":
        return "scout"
    if filter_key == "trainer":
        return "trainer"
    return "coach"


def _role_rating(rr: dict[str, str] | None, filter_key: str) -> float | None:
    if not rr:
        return None
    return _float_attr(rr, _rating_column_for_browse(filter_key))


def _meets_browse_filter(rr: dict[str, str] | None, filter_key: str, min_rating: float) -> bool:
    val = _role_rating(rr, filter_key)
    if val is None or val < min_rating:
        return False
    if filter_key in ("head_coach", "assistant_coach"):
        return True
    bucket = _staff_role_bucket(rr)
    if filter_key == "scout":
        return bucket == "scouts"
    if filter_key == "trainer":
        return bucket == "trainers"
    return True


def _load_catalog() -> dict[str, dict[str, Any]]:
    global _catalog_by_mtime, _catalog_key
    raw_dir = (
        Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
        if has_app_context()
        else Path(Config.RAW_IMPORT_DIR)
    )
    mp = raw_dir / "staff_master.csv"
    rp = raw_dir / "staff_ratings.csv"
    if not mp.is_file() or not rp.is_file():
        _catalog_by_mtime = {}
        _catalog_key = (0.0, 0.0)
        return _catalog_by_mtime

    key = (mp.stat().st_mtime, rp.stat().st_mtime)
    if _catalog_by_mtime is not None and _catalog_key == key:
        return _catalog_by_mtime

    ratings_by_id = _read_staff_ratings_by_id(rp)
    out: dict[str, dict[str, Any]] = {}
    master_df = read_csv_normalized(mp)
    for _, mrow in master_df.iterrows():
        m = mrow.to_dict()
        if to_int(cell_val(m, "retired"), 0) == 1:
            continue
        sid = cell_val(m, "staffid")
        if not sid:
            continue
        sid_s = str(sid).strip()
        rr = ratings_by_id.get(sid_s)
        fn = (cell_val(m, "first_name") or "").strip()
        ln = (cell_val(m, "last_name") or "").strip()
        full = f"{fn} {ln}".strip() or "—"
        nick = (cell_val(m, "nick_name") or "").strip()
        if nick:
            full = f'{full} "{nick}"'
        nat = (cell_val(m, "nationality_one") or "").strip() or "—"
        tid_raw = cell_val(m, "teamid")
        fhm_team_id = str(tid_raw).strip() if tid_raw is not None else ""
        attrs: dict[str, float | None] = {}
        for k in _all_staff_attr_keys():
            attrs[k] = _float_attr(rr, k)
        out[sid_s] = {
            "staff_fhm_id": sid_s,
            "full_name": full,
            "nationality": nat,
            "fhm_team_id": fhm_team_id if fhm_team_id not in ("", "-1") else "",
            "attrs": attrs,
            "ratings_row": rr or {},
            "primary_bucket": _staff_role_bucket(rr),
            "coach_rating": _float_attr(rr, "coach"),
            "scout_rating": _float_attr(rr, "scout"),
            "trainer_rating": _float_attr(rr, "trainer"),
        }

    _catalog_by_mtime = out
    _catalog_key = key
    return out


def get_staff_profile(staff_fhm_id: str | int | None) -> dict[str, Any] | None:
    sid = str(staff_fhm_id or "").strip()
    if not sid:
        return None
    return _load_catalog().get(sid)


def compute_staff_role_overall(
    attrs: dict[str, Any] | None,
    column_keys: tuple[str, ...] | list[str],
) -> int | None:
    """Role overall: mean of displayed attribute columns (0–20), rounded."""
    if not attrs:
        return None
    vals: list[float] = []
    for key in column_keys:
        v = attrs.get(key)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return int(round(sum(vals) / len(vals)))


def list_staff_for_browse(
    filter_key: str,
    *,
    min_rating: float = _MIN_RATING,
    exclude_staff_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    ex = exclude_staff_ids or set()
    rows: list[dict[str, Any]] = []
    for sid, entry in _load_catalog().items():
        if sid in ex:
            continue
        rr = entry.get("ratings_row") or {}
        if not _meets_browse_filter(rr, filter_key, min_rating):
            continue
        rating = _role_rating(rr, filter_key)
        col_keys = browse_column_keys(filter_key)
        role_overall = compute_staff_role_overall(entry.get("attrs"), col_keys)
        rows.append(
            {
                **entry,
                "browse_rating": rating,
                "browse_filter": filter_key,
                "role_overall": role_overall,
            }
        )
    rows.sort(
        key=lambda r: (
            -(int(r.get("role_overall") or 0)),
            str(r.get("full_name") or "").lower(),
        )
    )
    return rows


def browse_column_keys(filter_key: str) -> tuple[str, ...]:
    if filter_key in ("head_coach", "assistant_coach"):
        return tuple(k for k, _ in coach_columns())
    if filter_key == "scout":
        return tuple(k for k, _ in scout_columns())
    if filter_key == "trainer":
        return tuple(k for k, _ in trainer_columns())
    return ()


def coach_columns() -> tuple[tuple[str, tuple[str, str]], ...]:
    return STAFF_COACH_COLUMNS


def scout_columns() -> tuple[tuple[str, tuple[str, str]], ...]:
    return STAFF_SCOUT_COLUMNS


def trainer_columns() -> tuple[tuple[str, tuple[str, str]], ...]:
    return STAFF_TRAINER_COLUMNS
