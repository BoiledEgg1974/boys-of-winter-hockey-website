"""Verify SITE_DATABASE_URL can connect to the shared site MySQL database."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.config import normalize_site_database_url

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main() -> None:
    site_url = normalize_site_database_url(str(os.environ.get("SITE_DATABASE_URL") or "").strip())
    if not site_url.startswith("mysql"):
        print(
            "SITE_DATABASE_URL must be a mysql+pymysql:// URL. "
            "Set it in .env or the PythonAnywhere Web tab.",
            file=sys.stderr,
        )
        sys.exit(1)

    engine = create_engine(site_url, pool_pre_ping=True)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT VERSION()")).scalar_one()
        db_name = conn.execute(text("SELECT DATABASE()")).scalar_one()
        print(f"MySQL connection OK (server {version}, database {db_name})")
        if db_name and "%" in str(os.environ.get("SITE_DATABASE_URL") or ""):
            print("Note: SITE_DATABASE_URL used %24 for $; app normalizes this automatically.")


if __name__ == "__main__":
    main()
