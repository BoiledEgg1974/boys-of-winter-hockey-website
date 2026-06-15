"""Copy instance/site_membership.db into the MySQL SITE_DATABASE_URL database.

Run on PythonAnywhere after creating the MySQL database and setting SITE_DATABASE_URL.

Example:
  export SITE_DATABASE_URL='mysql+pymysql://BoiledEgg1974:PASSWORD@BoiledEgg1974.mysql.pythonanywhere-services.com/BoiledEgg1974%24bowlsite'
  python scripts/verify_site_mysql_connection.py
  python scripts/migrate_site_sqlite_to_mysql.py --dry-run
  python scripts/migrate_site_sqlite_to_mysql.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import Table

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

DEFAULT_SQLITE = ROOT / "instance" / "site_membership.db"
BATCH_SIZE = 500


def _site_tables() -> list[Table]:
    import app.site_models  # noqa: F401
    from app.league_db import db

    return list(db.metadatas["site"].sorted_tables)


def _sqlite_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _table_row_count(engine: Engine, table_name: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar_one() or 0)


def _mysql_has_rows(engine: Engine, tables: list[Table]) -> bool:
    for table in tables:
        if _table_row_count(engine, table.name) > 0:
            return True
    return False


def _copy_table(source: Engine, dest_conn, table: Table) -> int:
    if table.name not in _sqlite_tables(source):
        print(f"  skip {table.name}: not in SQLite")
        return 0

    with source.connect() as src_conn:
        result = src_conn.execute(table.select())
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    if not rows:
        print(f"  {table.name}: 0 rows")
        return 0

    copied = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        dest_conn.execute(table.insert(), batch)
        copied += len(batch)
    print(f"  {table.name}: copied {copied} rows")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate site_membership.db to MySQL")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=DEFAULT_SQLITE,
        help=f"Source SQLite file (default: {DEFAULT_SQLITE})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show row counts only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination tables even if MySQL already has rows",
    )
    args = parser.parse_args()

    site_url = str(os.environ.get("SITE_DATABASE_URL") or "").strip()
    sqlite_path = args.sqlite_path.resolve()
    if not sqlite_path.is_file():
        print(f"SQLite source not found: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    tables = _site_tables()
    source_engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}")

    print(f"Source: {sqlite_path}")
    print(f"Tables: {len(tables)}")

    if args.dry_run:
        if site_url:
            print(f"Destination: {site_url.split('@', 1)[-1]}")
        total = 0
        for table in tables:
            if table.name not in _sqlite_tables(source_engine):
                print(f"  {table.name}: missing in SQLite")
                continue
            count = _table_row_count(source_engine, table.name)
            print(f"  {table.name}: {count} rows")
            total += count
        print(f"Total rows to copy: {total}")
        return

    if not site_url.startswith("mysql"):
        print("SITE_DATABASE_URL must be set to a mysql+pymysql:// URL.", file=sys.stderr)
        sys.exit(1)

    dest_engine = create_engine(site_url, pool_pre_ping=True)
    print(f"Destination: {site_url.split('@', 1)[-1]}")

    if _mysql_has_rows(dest_engine, tables) and not args.force:
        print(
            "Destination MySQL already has site rows. Re-run with --force to overwrite, "
            "or use a fresh empty database.",
            file=sys.stderr,
        )
        sys.exit(1)

    from hub import create_hub_app

    hub = create_hub_app()
    with hub.app_context():
        from app.league_db import db

        db.create_all()
        site_engine = db.engines.get("site")
        if site_engine is None:
            print("Site engine missing after create_all()", file=sys.stderr)
            sys.exit(1)

        if args.force:
            with site_engine.begin() as conn:
                if site_engine.dialect.name == "mysql":
                    conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                for table in reversed(tables):
                    conn.execute(text(f"DELETE FROM `{table.name}`"))
                if site_engine.dialect.name == "mysql":
                    conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

        total = 0
        with site_engine.begin() as conn:
            if site_engine.dialect.name == "mysql":
                conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in tables:
                total += _copy_table(source_engine, conn, table)
            if site_engine.dialect.name == "mysql":
                conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    print(f"Migration complete. Copied {total} rows.")
    print("Next: reload the PythonAnywhere web app and bot with SITE_DATABASE_URL set.")


if __name__ == "__main__":
    main()
