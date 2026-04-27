"""Import AP redemption catalog rows from JSON into local site SQLite database."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace ap_redemption_catalog rows using a JSON export.",
    )
    parser.add_argument(
        "--in",
        dest="in_file",
        default="ap_redemption_catalog_live.json",
        help="Input JSON path (default: ap_redemption_catalog_live.json).",
    )
    parser.add_argument(
        "--db",
        default="instance/site_membership.db",
        help="Path to local site_membership.db (default: instance/site_membership.db).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing a .bak copy before import.",
    )
    return parser.parse_args()


def _to_db_row(item: dict) -> tuple[str, int, str, str, int, int]:
    return (
        str(item.get("league_group", "")).strip(),
        int(item.get("sort_order", 0) or 0),
        str(item.get("title", "")).strip(),
        str(item.get("description", "") or "").strip(),
        int(item.get("cost_ap", 0) or 0),
        1 if bool(item.get("is_active")) else 0,
    )


def main() -> None:
    args = parse_args()
    in_path = Path(args.in_file)
    db_path = Path(args.db)
    if not in_path.is_file():
        raise SystemExit(f"JSON file not found: {in_path}")
    if not db_path.is_file():
        raise SystemExit(f"Database file not found: {db_path}")

    rows_raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(rows_raw, list):
        raise SystemExit("JSON must be a list of row objects.")
    rows = [_to_db_row(r) for r in rows_raw if isinstance(r, dict)]
    if not rows:
        raise SystemExit("No valid rows found in JSON; aborting.")

    if not args.no_backup:
        backup_path = db_path.with_suffix(db_path.suffix + ".bak")
        shutil.copy2(db_path, backup_path)
        print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ap_redemption_catalog")
        cur.executemany(
            """
            INSERT INTO ap_redemption_catalog
            (league_group, sort_order, title, description, cost_ap, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Imported {len(rows)} rows into {db_path}")


if __name__ == "__main__":
    main()
