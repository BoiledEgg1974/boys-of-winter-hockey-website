"""Create a dated backup of league SQLite files and shared site data.

Backs up per-league data (Hall of Fame, Game Records baselines, FHM stats, trades)
and site-wide data (AP ledger, attendance, GM accounts, news, Discord queues, BOWL Six).

Run on the live server after reloading the web app (and pausing the Discord bot) so
SQLite files are not locked.

Examples::

    python scripts/backup_all_live_data.py
    python scripts/backup_all_live_data.py --out instance/full_backups/manual-before-import
    python scripts/backup_all_live_data.py --no-json
    python scripts/backup_all_live_data.py --verify
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.config import (  # noqa: E402
    BASE_DIR,
    league_slugs,
    normalize_site_database_url,
    resolve_league_sqlite_path,
    resolve_site_sqlite_path,
)
from app.db_utils import sqlite_integrity_message, sqlite_wal_checkpoint  # noqa: E402

DEFAULT_BACKUP_ROOT = BASE_DIR / "instance" / "full_backups"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _redact_database_url(url: str) -> str:
    raw = str(url or "").strip()
    if "@" not in raw:
        return raw
    prefix, host_part = raw.split("@", 1)
    if "://" in prefix:
        scheme, rest = prefix.split("://", 1)
        if ":" in rest:
            user = rest.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host_part}"
    return f"***@{host_part}"


def _site_tables() -> list[str]:
    import app.site_models  # noqa: F401
    from app.league_db import db

    return [table.name for table in db.metadatas["site"].sorted_tables]


def copy_sqlite_database(
    src: Path,
    dest: Path,
    *,
    checkpoint: bool = True,
    verify: bool = False,
) -> dict:
    """Copy a SQLite file and any WAL/SHM sidecars."""
    src = src.resolve()
    dest = dest.resolve()
    if not src.is_file():
        return {"ok": False, "source": str(src), "message": "source missing"}

    if checkpoint:
        sqlite_wal_checkpoint(src)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    copied_sidecars: list[str] = []
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        sidecar = src.parent / (src.name + suffix)
        if sidecar.is_file():
            side_dest = dest.parent / (dest.name + suffix)
            shutil.copy2(sidecar, side_dest)
            copied_sidecars.append(side_dest.name)

    integrity = sqlite_integrity_message(dest) if verify else "skipped"
    if verify and integrity.lower() not in ("ok", "skipped"):
        return {
            "ok": False,
            "source": str(src),
            "dest": str(dest),
            "sidecars": copied_sidecars,
            "integrity": integrity,
            "message": f"integrity check failed: {integrity}",
        }

    return {
        "ok": True,
        "source": str(src),
        "dest": str(dest),
        "size_bytes": dest.stat().st_size,
        "sidecars": copied_sidecars,
        "integrity": integrity,
    }


def export_site_tables_json(engine: Engine, dest_dir: Path) -> list[dict]:
    """Export every site table to ``<dest_dir>/<table>.json``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = set(inspect(engine).get_table_names())
    summaries: list[dict] = []

    for table_name in _site_tables():
        out_path = dest_dir / f"{table_name}.json"
        if table_name not in existing:
            out_path.write_text("[]\n", encoding="utf-8")
            summaries.append({"table": table_name, "rows": 0, "path": str(out_path), "missing": True})
            continue

        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM `{table_name}`"))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]

        serializable: list[dict] = []
        for row in rows:
            clean: dict = {}
            for key, value in row.items():
                if hasattr(value, "isoformat"):
                    clean[key] = value.isoformat()
                else:
                    clean[key] = value
            serializable.append(clean)

        out_path.write_text(json.dumps(serializable, indent=2, default=str) + "\n", encoding="utf-8")
        summaries.append({"table": table_name, "rows": len(serializable), "path": str(out_path)})

    return summaries


def backup_league_databases(
    out_dir: Path,
    *,
    checkpoint: bool = True,
    verify: bool = False,
) -> list[dict]:
    league_dir = out_dir / "league"
    league_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for slug in league_slugs():
        src = resolve_league_sqlite_path(slug)
        dest = league_dir / f"{slug}.db"
        info = copy_sqlite_database(src, dest, checkpoint=checkpoint, verify=verify)
        info["slug"] = slug
        results.append(info)

    return results


def backup_site_database(
    out_dir: Path,
    *,
    checkpoint: bool = True,
    verify: bool = False,
    include_json: bool = True,
) -> dict:
    site_dir = out_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    site_url = normalize_site_database_url(str(os.environ.get("SITE_DATABASE_URL") or "").strip())
    sqlite_path = resolve_site_sqlite_path()

    if site_url.startswith("mysql"):
        engine = create_engine(site_url)
        try:
            tables_dir = site_dir / "tables"
            table_summaries = export_site_tables_json(engine, tables_dir)
            total_rows = sum(int(row.get("rows") or 0) for row in table_summaries)
            return {
                "ok": True,
                "backend": "mysql",
                "database_url": _redact_database_url(site_url),
                "tables_dir": str(tables_dir),
                "table_count": len(table_summaries),
                "total_rows": total_rows,
                "tables": table_summaries,
            }
        finally:
            engine.dispose()

    if not sqlite_path.is_file():
        return {
            "ok": False,
            "backend": "sqlite",
            "message": f"Site SQLite not found: {sqlite_path}",
        }

    dest = site_dir / "site_membership.db"
    copy_info = copy_sqlite_database(sqlite_path, dest, checkpoint=checkpoint, verify=verify)
    result = {
        "ok": copy_info.get("ok", False),
        "backend": "sqlite",
        "source": str(sqlite_path),
        "dest": str(dest),
        "size_bytes": copy_info.get("size_bytes"),
        "sidecars": copy_info.get("sidecars", []),
        "integrity": copy_info.get("integrity"),
    }
    if copy_info.get("message"):
        result["message"] = copy_info["message"]

    if include_json and result["ok"]:
        engine = create_engine(f"sqlite:///{dest.as_posix()}")
        try:
            json_dir = site_dir / "tables"
            table_summaries = export_site_tables_json(engine, json_dir)
            result["tables_dir"] = str(json_dir)
            result["table_count"] = len(table_summaries)
            result["total_rows"] = sum(int(row.get("rows") or 0) for row in table_summaries)
        finally:
            engine.dispose()

    return result


def export_league_json_supplements(out_dir: Path) -> list[dict]:
    """Export trade logs and OVR baselines per league as JSON."""
    from scripts.ovr_baseline_transfer import export_ovr_baselines_json
    from scripts.trade_log_transfer import export_trade_log_json

    json_dir = out_dir / "json" / "league"
    json_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for slug in league_slugs():
        db_path = resolve_league_sqlite_path(slug)
        trade_out = json_dir / f"{slug}_trade_log.json"
        ovr_out = json_dir / f"{slug}_ovr_baselines.json"
        slug_result: dict = {"slug": slug, "source": str(db_path)}

        if not db_path.is_file():
            slug_result["ok"] = False
            slug_result["message"] = "league database missing"
            results.append(slug_result)
            continue

        try:
            trade_count = export_trade_log_json(db_path, trade_out)
            ovr_count = export_ovr_baselines_json(db_path, ovr_out)
        except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            slug_result["ok"] = False
            slug_result["message"] = str(exc)
            results.append(slug_result)
            continue

        slug_result.update(
            {
                "ok": True,
                "trade_log_rows": trade_count,
                "trade_log_path": str(trade_out),
                "ovr_baseline_rows": ovr_count,
                "ovr_baselines_path": str(ovr_out),
            }
        )
        results.append(slug_result)

    return results


def run_backup(
    out_dir: Path,
    *,
    checkpoint: bool = True,
    verify: bool = False,
    include_json: bool = True,
) -> dict:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backup_dir": str(out_dir),
        "checkpoint_before_copy": checkpoint,
        "verified_integrity": verify,
        "notes": [
            "Reload the web app and pause the Discord bot before backing up on a live server.",
            "League .db files include Hall of Fame, Game Record baselines, and FHM stats.",
            "Site backup includes AP ledger, attendance, GM accounts, news, and BOWL Six.",
        ],
    }

    manifest["leagues"] = backup_league_databases(out_dir, checkpoint=checkpoint, verify=verify)
    manifest["site"] = backup_site_database(
        out_dir,
        checkpoint=checkpoint,
        verify=verify,
        include_json=include_json,
    )
    if include_json:
        manifest["league_json"] = export_league_json_supplements(out_dir)

    league_failures = [
        row
        for row in manifest["leagues"]
        if row.get("message") and row.get("message") != "source missing"
    ]
    manifest["ok"] = bool(manifest["site"].get("ok")) and not league_failures

    manifest_path = out_dir / "backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up league SQLite files and shared site data into one folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Backup directory (default: instance/full_backups/<UTC timestamp>).",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Skip WAL checkpoint before copying SQLite files.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run PRAGMA integrity_check on copied SQLite files.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip JSON supplements (trade logs, OVR baselines, site table exports for SQLite).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out
    if out_dir is None:
        out_dir = DEFAULT_BACKUP_ROOT / _utc_timestamp()

    manifest = run_backup(
        out_dir,
        checkpoint=not args.no_checkpoint,
        verify=args.verify,
        include_json=not args.no_json,
    )

    print(f"Backup directory: {manifest['backup_dir']}")
    print(f"Manifest: {manifest['manifest_path']}")

    for league in manifest.get("leagues", []):
        slug = league.get("slug", "?")
        if league.get("ok"):
            size_kb = int(league.get("size_bytes") or 0) // 1024
            print(f"  league {slug}: OK ({size_kb} KiB)")
        elif league.get("message") == "source missing":
            print(f"  league {slug}: skipped (database not found)")
        else:
            print(f"  league {slug}: FAILED — {league.get('message', 'unknown error')}")

    site = manifest.get("site", {})
    if site.get("ok"):
        if site.get("backend") == "mysql":
            print(
                f"  site (MySQL): OK ({site.get('table_count', 0)} tables, "
                f"{site.get('total_rows', 0)} rows)"
            )
        else:
            size_kb = int(site.get("size_bytes") or 0) // 1024
            print(f"  site (SQLite): OK ({size_kb} KiB)")
    else:
        print(f"  site: FAILED — {site.get('message', 'unknown error')}")

    if manifest.get("league_json"):
        for row in manifest["league_json"]:
            if row.get("ok"):
                print(
                    f"  json {row['slug']}: trade_log={row.get('trade_log_rows', 0)}, "
                    f"ovr={row.get('ovr_baseline_rows', 0)}"
                )

    if not manifest.get("ok"):
        print("Backup completed with errors.", file=sys.stderr)
        return 1

    print("Backup completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
