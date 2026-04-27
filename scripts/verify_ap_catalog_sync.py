"""Verify local AP catalog matches a JSON snapshot, printing a readable diff."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

RowKey = tuple[str, int, str, str, int, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare local ap_redemption_catalog against a JSON snapshot.",
    )
    parser.add_argument(
        "--in",
        dest="in_file",
        default="ap_redemption_catalog_live.json",
        help="Input JSON snapshot path (default: ap_redemption_catalog_live.json).",
    )
    parser.add_argument(
        "--db",
        default="instance/site_membership.db",
        help="Path to local site_membership.db (default: instance/site_membership.db).",
    )
    return parser.parse_args()


def normalize_row(item: dict) -> RowKey:
    return (
        str(item.get("league_group", "")).strip(),
        int(item.get("sort_order", 0) or 0),
        str(item.get("title", "")).strip(),
        str(item.get("description", "") or "").strip(),
        int(item.get("cost_ap", 0) or 0),
        1 if bool(item.get("is_active")) else 0,
    )


def load_snapshot_rows(path: Path) -> list[RowKey]:
    if not path.is_file():
        raise SystemExit(f"JSON snapshot not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("Snapshot JSON must be a list of objects.")
    return [normalize_row(r) for r in raw if isinstance(r, dict)]


def load_db_rows(path: Path) -> list[RowKey]:
    if not path.is_file():
        raise SystemExit(f"Database file not found: {path}")
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            """
            SELECT league_group, sort_order, title, description, cost_ap, is_active
            FROM ap_redemption_catalog
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[RowKey] = []
    for league_group, sort_order, title, description, cost_ap, is_active in rows:
        out.append(
            (
                str(league_group or "").strip(),
                int(sort_order or 0),
                str(title or "").strip(),
                str(description or "").strip(),
                int(cost_ap or 0),
                1 if bool(is_active) else 0,
            )
        )
    return out


def _print_rows(label: str, rows: list[RowKey], limit: int = 25) -> None:
    print(f"{label}: {len(rows)}")
    for i, row in enumerate(rows[:limit], start=1):
        g, s, t, d, c, a = row
        active_txt = "active" if a else "inactive"
        print(f"  {i:>2}. [{g}] cost={c} sort={s} {active_txt} title={t!r} desc={d!r}")
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more")


def main() -> None:
    args = parse_args()
    snapshot_rows = load_snapshot_rows(Path(args.in_file))
    db_rows = load_db_rows(Path(args.db))

    snapshot_counter = Counter(snapshot_rows)
    db_counter = Counter(db_rows)

    extra_counter = db_counter - snapshot_counter
    missing_counter = snapshot_counter - db_counter

    extras = sorted(extra_counter.elements())
    missing = sorted(missing_counter.elements())

    print(f"Snapshot rows: {len(snapshot_rows)}")
    print(f"Local DB rows: {len(db_rows)}")

    if not extras and not missing:
        print("MATCH: local AP catalog is identical to snapshot.")
        return

    print("DIFF: local AP catalog does not match snapshot.")
    if missing:
        _print_rows("Missing in local (present in snapshot)", missing)
    if extras:
        _print_rows("Extra in local (not in snapshot)", extras)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
