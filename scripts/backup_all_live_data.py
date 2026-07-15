"""Create a dated backup of league SQLite files and shared site data.

Backs up per-league databases (awards history, records, Hall of Fame, archived
season stats/standings, FHM stats, trades, team honors rows) and site-wide data
(AP ledger, attendance, GM accounts, news, Discord queues, BOWL Six, boost
records, admin settings). Also copies file-based Join League open-team lists,
team-page honors panel images (retired jerseys / victory banners), and writes
readable JSON dumps for key categories (unless ``--no-json``).

By default, when the new backup is written under ``instance/full_backups/``, older
backup folders there are pruned so at most 3 versions remain.

Run on the live server after reloading the web app (and pausing the Discord bot) so
SQLite files are not locked.

Examples::

    python scripts/backup_all_live_data.py
    python scripts/backup_all_live_data.py --out instance/full_backups/manual-before-import
    python scripts/backup_all_live_data.py --keep 3
    python scripts/backup_all_live_data.py --no-prune
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
DEFAULT_KEEP_BACKUPS = 3
_INSTANCE_DIR = BASE_DIR / "instance"
_TEAM_HONORS_STATIC_DIR = BASE_DIR / "app" / "static" / "team_honors"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")
_TEAM_HONORS_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

LEAGUE_COVERAGE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "awards": ("history_awards", "history_champions", "history_all_stars"),
    "records": ("game_record_baselines", "team_season_records", "record_stat_adjustments"),
    "hall_of_fame": ("hall_of_fame_members",),
    "team_honors": ("team_honors_meta", "team_retired_numbers", "team_victory_banners"),
    "archived_seasons": (
        "seasons",
        "team_standings",
        "player_skater_stats",
        "player_goalie_stats",
        "games",
    ),
}

SITE_COVERAGE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "boost_records": ("boost_lottery_team_results",),
    "admin_live_data": (
        "league_rule_settings",
        "league_salary_cap_years",
        "homepage_module_settings",
        "site_announcements",
        "discord_league_bot_config",
        "discord_channel_routes",
        "sim_cycle_state",
        "ap_redemption_catalog",
        "league_drafts",
        "bowl_six_slates",
        "draft_pick_ownership_years",
        "trade_market_draft_pick_ownership",
    ),
}

ADMIN_SETTINGS_TABLES: tuple[str, ...] = (
    "league_rule_settings",
    "league_salary_cap_years",
    "homepage_module_settings",
    "site_announcements",
    "discord_league_bot_config",
    "discord_channel_routes",
    "sim_cycle_state",
)

LEAGUE_AWARDS_TABLES: tuple[str, ...] = (
    "history_awards",
    "history_champions",
    "history_all_stars",
)
LEAGUE_RECORDS_TABLES: tuple[str, ...] = (
    "game_record_baselines",
    "team_season_records",
    "record_stat_adjustments",
)


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


def _serialize_row(row: dict) -> dict:
    clean: dict = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            clean[key] = value.isoformat()
        else:
            clean[key] = value
    return clean


def _sqlite_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_table_row_count(conn: sqlite3.Connection, table_name: str, existing: set[str]) -> dict:
    if table_name not in existing:
        return {"table": table_name, "rows": 0, "missing": True}
    count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    return {"table": table_name, "rows": int(count)}


def _category_inventory_from_sqlite(
    db_path: Path,
    categories: dict[str, tuple[str, ...]],
) -> dict:
    if not db_path.is_file():
        return {"ok": False, "message": "database missing", "categories": {}}

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        existing = _sqlite_table_names(conn)
        out_categories: dict[str, dict] = {}
        for category, tables in categories.items():
            table_rows = [_sqlite_table_row_count(conn, name, existing) for name in tables]
            out_categories[category] = {
                "tables": table_rows,
                "total_rows": sum(int(row["rows"]) for row in table_rows),
            }
        return {"ok": True, "source": str(db_path), "categories": out_categories}
    except sqlite3.Error as exc:
        return {"ok": False, "source": str(db_path), "message": str(exc), "categories": {}}
    finally:
        conn.close()


def _engine_table_row_counts(engine: Engine, tables: tuple[str, ...]) -> list[dict]:
    existing = set(inspect(engine).get_table_names())
    summaries: list[dict] = []
    with engine.connect() as conn:
        for table_name in tables:
            if table_name not in existing:
                summaries.append({"table": table_name, "rows": 0, "missing": True})
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
            summaries.append({"table": table_name, "rows": int(count or 0)})
    return summaries


def _fetch_table_rows_sqlite(conn: sqlite3.Connection, table_name: str, existing: set[str]) -> list[dict]:
    if table_name not in existing:
        return []
    cursor = conn.execute(f'SELECT * FROM "{table_name}"')
    columns = [col[0] for col in cursor.description]
    return [_serialize_row(dict(zip(columns, row))) for row in cursor.fetchall()]


def _fetch_table_rows_engine(engine: Engine, table_name: str, existing: set[str]) -> list[dict]:
    if table_name not in existing:
        return []
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM `{table_name}`"))
        columns = list(result.keys())
        return [_serialize_row(dict(zip(columns, row))) for row in result.fetchall()]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


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

        rows = _fetch_table_rows_engine(engine, table_name, existing)
        out_path.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")
        summaries.append({"table": table_name, "rows": len(rows), "path": str(out_path)})

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


def backup_admin_files(out_dir: Path, *, instance_dir: Path | None = None) -> dict:
    """Copy Join League open-team lists and related admin files from instance/."""
    inst = (instance_dir or _INSTANCE_DIR).resolve()
    admin_dir = out_dir / "admin_files"
    admin_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    missing: list[str] = []

    join_src = inst / "join_league"
    join_dest = admin_dir / "join_league"
    if join_src.is_dir():
        if join_dest.exists():
            shutil.rmtree(join_dest)
        shutil.copytree(join_src, join_dest)
        file_count = sum(1 for path in join_dest.rglob("*") if path.is_file())
        copied.append(
            {
                "ok": True,
                "source": str(join_src),
                "dest": str(join_dest),
                "file_count": file_count,
            }
        )
    else:
        missing.append(str(join_src))

    legacy_name = "join_league_available_teams.txt"
    legacy_src = inst / legacy_name
    if legacy_src.is_file():
        legacy_dest = admin_dir / legacy_name
        shutil.copy2(legacy_src, legacy_dest)
        copied.append(
            {
                "ok": True,
                "source": str(legacy_src),
                "dest": str(legacy_dest),
                "file_count": 1,
            }
        )
    else:
        missing.append(str(legacy_src))

    return {
        "ok": True,
        "dest": str(admin_dir),
        "copied": copied,
        "missing": missing,
    }


def backup_team_honors_media(
    out_dir: Path,
    *,
    source_dir: Path | None = None,
) -> dict:
    """Copy team-page honors panel images (retired jerseys / victory banners)."""
    src = (source_dir or _TEAM_HONORS_STATIC_DIR).resolve()
    dest = (out_dir / "static" / "team_honors").resolve()
    if not src.is_dir():
        return {
            "ok": True,
            "source": str(src),
            "dest": str(dest),
            "file_count": 0,
            "image_count": 0,
            "missing": True,
            "message": f"Team honors media folder not found: {src}",
        }

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    files = [path for path in dest.rglob("*") if path.is_file()]
    image_count = sum(1 for path in files if path.suffix.lower() in _TEAM_HONORS_IMAGE_EXTS)
    return {
        "ok": True,
        "source": str(src),
        "dest": str(dest),
        "file_count": len(files),
        "image_count": image_count,
        "missing": False,
    }


def build_coverage_inventory(
    league_results: list[dict],
    site_result: dict,
) -> dict:
    """Row-count inventory for awards, records, HoF, boosts, admin data, seasons."""
    leagues: list[dict] = []
    for row in league_results:
        slug = row.get("slug", "?")
        db_path = Path(str(row.get("dest") or "")) if row.get("ok") else Path()
        if not row.get("ok") or not db_path.is_file():
            leagues.append(
                {
                    "slug": slug,
                    "ok": False,
                    "message": row.get("message") or "league database not backed up",
                    "categories": {},
                }
            )
            continue
        inventory = _category_inventory_from_sqlite(db_path, LEAGUE_COVERAGE_CATEGORIES)
        inventory["slug"] = slug
        leagues.append(inventory)

    site_coverage: dict = {"ok": False, "categories": {}}
    if site_result.get("ok"):
        if site_result.get("backend") == "mysql":
            site_url = normalize_site_database_url(str(os.environ.get("SITE_DATABASE_URL") or "").strip())
            engine = create_engine(site_url)
            try:
                categories: dict[str, dict] = {}
                for category, tables in SITE_COVERAGE_CATEGORIES.items():
                    table_rows = _engine_table_row_counts(engine, tables)
                    categories[category] = {
                        "tables": table_rows,
                        "total_rows": sum(int(row["rows"]) for row in table_rows),
                    }
                site_coverage = {
                    "ok": True,
                    "backend": "mysql",
                    "categories": categories,
                }
            except Exception as exc:  # noqa: BLE001 — surface in manifest
                site_coverage = {"ok": False, "backend": "mysql", "message": str(exc), "categories": {}}
            finally:
                engine.dispose()
        else:
            dest = Path(str(site_result.get("dest") or ""))
            site_coverage = _category_inventory_from_sqlite(dest, SITE_COVERAGE_CATEGORIES)
            site_coverage["backend"] = "sqlite"
    else:
        site_coverage = {
            "ok": False,
            "message": site_result.get("message") or "site database not backed up",
            "categories": {},
        }

    return {"leagues": leagues, "site": site_coverage}


def export_league_history_json(db_path: Path, dest_dir: Path) -> dict:
    """Write awards/HoF/records/seasons_index JSON for one league database."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.is_file():
        return {"ok": False, "message": "league database missing"}

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        existing = _sqlite_table_names(conn)
        awards = {name: _fetch_table_rows_sqlite(conn, name, existing) for name in LEAGUE_AWARDS_TABLES}
        hof = _fetch_table_rows_sqlite(conn, "hall_of_fame_members", existing)
        records = {name: _fetch_table_rows_sqlite(conn, name, existing) for name in LEAGUE_RECORDS_TABLES}
        seasons = _fetch_table_rows_sqlite(conn, "seasons", existing)

        awards_path = dest_dir / "awards.json"
        hof_path = dest_dir / "hall_of_fame.json"
        records_path = dest_dir / "records.json"
        seasons_path = dest_dir / "seasons_index.json"
        _write_json(awards_path, awards)
        _write_json(hof_path, hof)
        _write_json(records_path, records)
        _write_json(seasons_path, seasons)

        return {
            "ok": True,
            "awards_rows": sum(len(rows) for rows in awards.values()),
            "awards_path": str(awards_path),
            "hall_of_fame_rows": len(hof),
            "hall_of_fame_path": str(hof_path),
            "records_rows": sum(len(rows) for rows in records.values()),
            "records_path": str(records_path),
            "seasons_index_rows": len(seasons),
            "seasons_index_path": str(seasons_path),
        }
    except sqlite3.Error as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def export_site_focused_json(engine: Engine, dest_dir: Path) -> dict:
    """Write boost_records.json and admin_settings.json."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = set(inspect(engine).get_table_names())

    boost_rows = _fetch_table_rows_engine(engine, "boost_lottery_team_results", existing)
    admin_payload = {
        name: _fetch_table_rows_engine(engine, name, existing) for name in ADMIN_SETTINGS_TABLES
    }

    boost_path = dest_dir / "boost_records.json"
    admin_path = dest_dir / "admin_settings.json"
    _write_json(boost_path, boost_rows)
    _write_json(admin_path, admin_payload)

    return {
        "ok": True,
        "boost_records_rows": len(boost_rows),
        "boost_records_path": str(boost_path),
        "admin_settings_rows": sum(len(rows) for rows in admin_payload.values()),
        "admin_settings_path": str(admin_path),
    }


def export_league_json_supplements(out_dir: Path, league_results: list[dict] | None = None) -> list[dict]:
    """Export trade logs, OVR baselines, and history-focused JSON per league."""
    from scripts.ovr_baseline_transfer import export_ovr_baselines_json
    from scripts.trade_log_transfer import export_trade_log_json

    json_dir = out_dir / "json" / "league"
    json_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    dest_by_slug = {
        str(row.get("slug")): Path(str(row["dest"]))
        for row in (league_results or [])
        if row.get("ok") and row.get("dest")
    }

    for slug in league_slugs():
        db_path = dest_by_slug.get(slug) or resolve_league_sqlite_path(slug)
        trade_out = json_dir / f"{slug}_trade_log.json"
        ovr_out = json_dir / f"{slug}_ovr_baselines.json"
        history_dir = json_dir / slug
        slug_result: dict = {"slug": slug, "source": str(db_path)}

        if not db_path.is_file():
            slug_result["ok"] = False
            slug_result["message"] = "league database missing"
            results.append(slug_result)
            continue

        trade_count = 0
        ovr_count = 0
        errors: list[str] = []
        try:
            trade_count = export_trade_log_json(db_path, trade_out)
        except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            errors.append(f"trade_log: {exc}")
        try:
            ovr_count = export_ovr_baselines_json(db_path, ovr_out)
        except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            errors.append(f"ovr: {exc}")

        history = export_league_history_json(db_path, history_dir)
        if not history.get("ok"):
            errors.append(history.get("message") or "history JSON export failed")

        slug_result.update(
            {
                "ok": bool(history.get("ok", False)),
                "trade_log_rows": trade_count,
                "trade_log_path": str(trade_out),
                "ovr_baseline_rows": ovr_count,
                "ovr_baselines_path": str(ovr_out),
                "history": history,
            }
        )
        if errors:
            slug_result["message"] = "; ".join(errors)
        results.append(slug_result)

    return results


def export_site_json_supplements(out_dir: Path, site_result: dict) -> dict:
    """Export focused site JSON (boosts + admin settings)."""
    dest_dir = out_dir / "json" / "site"
    if not site_result.get("ok"):
        return {"ok": False, "message": site_result.get("message") or "site database not backed up"}

    if site_result.get("backend") == "mysql":
        site_url = normalize_site_database_url(str(os.environ.get("SITE_DATABASE_URL") or "").strip())
        engine = create_engine(site_url)
        try:
            return export_site_focused_json(engine, dest_dir)
        finally:
            engine.dispose()

    dest = Path(str(site_result.get("dest") or ""))
    if not dest.is_file():
        return {"ok": False, "message": "site SQLite backup missing"}
    engine = create_engine(f"sqlite:///{dest.as_posix()}")
    try:
        return export_site_focused_json(engine, dest_dir)
    finally:
        engine.dispose()


def list_backup_versions(backup_root: Path) -> list[Path]:
    """Return completed backup folders under ``backup_root`` (newest first)."""
    backup_root = backup_root.resolve()
    if not backup_root.is_dir():
        return []

    candidates: list[Path] = []
    for child in backup_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "backup_manifest.json").is_file():
            candidates.append(child)

    candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return candidates


def prune_old_backups(backup_root: Path, *, keep: int = DEFAULT_KEEP_BACKUPS) -> dict:
    """Delete older completed backups under ``backup_root``, keeping the newest ``keep``.

    Only removes immediate child directories that contain ``backup_manifest.json``.
    """
    if keep < 1:
        raise ValueError("keep must be >= 1")

    backup_root = backup_root.resolve()
    versions = list_backup_versions(backup_root)
    retained = versions[:keep]
    removed: list[dict] = []
    errors: list[dict] = []

    for old in versions[keep:]:
        try:
            shutil.rmtree(old)
            removed.append({"ok": True, "path": str(old)})
        except OSError as exc:
            errors.append({"ok": False, "path": str(old), "message": str(exc)})

    return {
        "ok": not errors,
        "backup_root": str(backup_root),
        "keep": keep,
        "retained": [str(path) for path in retained],
        "removed": removed,
        "errors": errors,
    }


def retention_root_for(out_dir: Path, *, default_root: Path | None = None) -> Path | None:
    """Return the folder whose backup children should be pruned, if any."""
    root = (default_root or DEFAULT_BACKUP_ROOT).resolve()
    out_dir = out_dir.resolve()
    if out_dir.parent == root:
        return root
    return None


def run_backup(
    out_dir: Path,
    *,
    checkpoint: bool = True,
    verify: bool = False,
    include_json: bool = True,
    instance_dir: Path | None = None,
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
            "League .db files include awards history, records, Hall of Fame, and archived season stats/standings.",
            "Site backup includes AP ledger, attendance, GM accounts, news, Discord, BOWL Six, boost records, and admin settings.",
            "coverage lists row counts for those categories; json/ holds readable dumps when JSON is enabled.",
            "admin_files/ includes Join League open-team lists from instance/join_league/.",
            "static/team_honors/ includes retired jersey and victory banner panel images for all leagues.",
            f"By default at most {DEFAULT_KEEP_BACKUPS} completed backups are kept under instance/full_backups/.",
        ],
    }

    manifest["leagues"] = backup_league_databases(out_dir, checkpoint=checkpoint, verify=verify)
    manifest["site"] = backup_site_database(
        out_dir,
        checkpoint=checkpoint,
        verify=verify,
        include_json=include_json,
    )
    manifest["admin_files"] = backup_admin_files(out_dir, instance_dir=instance_dir)
    manifest["team_honors_media"] = backup_team_honors_media(out_dir)
    manifest["coverage"] = build_coverage_inventory(manifest["leagues"], manifest["site"])

    if include_json:
        manifest["league_json"] = export_league_json_supplements(out_dir, manifest["leagues"])
        manifest["site_json"] = export_site_json_supplements(out_dir, manifest["site"])

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
        help=(
            "Skip JSON supplements (trade logs, OVR baselines, awards/HoF/records dumps, "
            "site table exports for SQLite)."
        ),
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP_BACKUPS,
        help=(
            f"Keep this many newest completed backups under instance/full_backups/ "
            f"(default: {DEFAULT_KEEP_BACKUPS}). Ignored with --no-prune."
        ),
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Do not delete older backups under instance/full_backups/.",
    )
    return parser.parse_args()


def _print_coverage(coverage: dict) -> None:
    for league in coverage.get("leagues") or []:
        slug = league.get("slug", "?")
        if not league.get("ok"):
            print(f"  coverage {slug}: skipped ({league.get('message', 'unavailable')})")
            continue
        cats = league.get("categories") or {}
        parts = [
            f"{name}={int((info or {}).get('total_rows') or 0)}"
            for name, info in cats.items()
        ]
        print(f"  coverage {slug}: " + ", ".join(parts))

    site = coverage.get("site") or {}
    if not site.get("ok"):
        print(f"  coverage site: skipped ({site.get('message', 'unavailable')})")
        return
    cats = site.get("categories") or {}
    parts = [
        f"{name}={int((info or {}).get('total_rows') or 0)}"
        for name, info in cats.items()
    ]
    print("  coverage site: " + ", ".join(parts))


def main() -> int:
    args = parse_args()
    out_dir = args.out
    if out_dir is None:
        out_dir = DEFAULT_BACKUP_ROOT / _utc_timestamp()

    if args.keep < 1:
        print("--keep must be >= 1", file=sys.stderr)
        return 2

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

    admin_files = manifest.get("admin_files") or {}
    copied = admin_files.get("copied") or []
    if copied:
        total_files = sum(int(row.get("file_count") or 0) for row in copied)
        print(f"  admin_files: OK ({total_files} file(s))")
    else:
        print("  admin_files: none found (join_league optional)")

    honors = manifest.get("team_honors_media") or {}
    if honors.get("missing"):
        print("  team_honors_media: none found (optional)")
    elif honors.get("ok"):
        print(
            f"  team_honors_media: OK "
            f"({int(honors.get('image_count') or 0)} image(s), "
            f"{int(honors.get('file_count') or 0)} file(s))"
        )
    else:
        print(f"  team_honors_media: FAILED — {honors.get('message', 'unknown error')}")

    if manifest.get("coverage"):
        _print_coverage(manifest["coverage"])

    if manifest.get("league_json"):
        for row in manifest["league_json"]:
            if row.get("ok"):
                history = row.get("history") or {}
                print(
                    f"  json {row['slug']}: trade_log={row.get('trade_log_rows', 0)}, "
                    f"ovr={row.get('ovr_baseline_rows', 0)}, "
                    f"awards={history.get('awards_rows', 0)}, "
                    f"hof={history.get('hall_of_fame_rows', 0)}, "
                    f"records={history.get('records_rows', 0)}, "
                    f"seasons={history.get('seasons_index_rows', 0)}"
                )

    site_json = manifest.get("site_json") or {}
    if site_json.get("ok"):
        print(
            f"  json site: boost={site_json.get('boost_records_rows', 0)}, "
            f"admin={site_json.get('admin_settings_rows', 0)}"
        )

    retention_root = None if args.no_prune else retention_root_for(Path(manifest["backup_dir"]))
    if retention_root is not None:
        retention = prune_old_backups(retention_root, keep=args.keep)
        manifest["retention"] = retention
        try:
            Path(manifest["manifest_path"]).write_text(
                json.dumps({k: v for k, v in manifest.items() if k != "manifest_path"}, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        removed_n = len(retention.get("removed") or [])
        retained_n = len(retention.get("retained") or [])
        print(f"  retention: kept {retained_n}, removed {removed_n} (limit {args.keep})")
        for err in retention.get("errors") or []:
            print(f"  retention FAILED — {err.get('path')}: {err.get('message')}", file=sys.stderr)
    elif args.no_prune:
        print("  retention: skipped (--no-prune)")

    if not manifest.get("ok"):
        print("Backup completed with errors.", file=sys.stderr)
        return 1

    print("Backup completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
