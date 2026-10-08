#!/usr/bin/env python3
"""Import site DB rows from backup_all_live_data JSON exports into MySQL."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.config import normalize_site_database_url

BATCH_SIZE = 500


def _sanitize_rows(table_name: str, rows: list[dict]) -> list[dict]:
    """Match legacy PA rows that predate strict discord_user_id uniqueness."""
    if table_name != "site_users":
        return rows
    seen_discord: set[str] = set()
    out: list[dict] = []
    for row in rows:
        row = dict(row)
        did = row.get("discord_user_id")
        if did:
            key = str(did)
            if key in seen_discord:
                row["discord_user_id"] = None
            else:
                seen_discord.add(key)
        out.append(row)
    return out


def _site_tables():
    import app.site_models  # noqa: F401
    from app.league_db import db

    return list(db.metadatas["site"].sorted_tables)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tables_dir", type=Path, help="site/tables from backup_all_live_data")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing rows before import (required for re-run)",
    )
    args = parser.parse_args()
    tables_dir = args.tables_dir.resolve()
    if not tables_dir.is_dir():
        print(f"Not a directory: {tables_dir}", file=sys.stderr)
        return 1

    site_url = normalize_site_database_url(str(os.environ.get("SITE_DATABASE_URL") or ""))
    if not site_url.startswith("mysql"):
        print("SITE_DATABASE_URL must be MySQL", file=sys.stderr)
        return 1

    import app.site_models  # noqa: F401
    from app.league_db import db

    engine = create_engine(site_url, pool_pre_ping=True)
    db.metadatas["site"].create_all(bind=engine)

    tables = _site_tables()
    json_by_name = {p.stem: p for p in tables_dir.glob("*.json")}

    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        if args.force:
            for table in reversed(tables):
                conn.execute(text(f"DELETE FROM `{table.name}`"))

        total = 0
        for table in tables:
            path = json_by_name.get(table.name)
            if not path:
                print(f"skip {table.name}: no JSON file")
                continue
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list) or not rows:
                print(f"skip empty {table.name}")
                continue
            rows = _sanitize_rows(table.name, rows)
            copied = 0
            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start : start + BATCH_SIZE]
                conn.execute(table.insert(), batch)
                copied += len(batch)
            total += copied
            print(f"imported {table.name}: {copied} rows")

        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    print(f"done ({total} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
