"""Export/import league editorial + accumulated history for deploy-db preserve.

Captures live-only / admin-sensitive league SQLite data before a local DB upload
replaces the live file. Merge rule is **gap-fill from live**: restore rows that are
missing locally; keep local rows when the same key already exists. Live ``admin``
rows still force-overwrite local non-admin.

Tables covered:

- record_stat_adjustments
- team_honors_meta, team_retired_numbers, team_victory_banners
- hall_of_fame_members (insert-if-missing; live admin overwrites)
- history_awards, history_all_stars (admin force + CSV gap-fill)
- team_season_records (prefer live admin + import)
- franchise_team_identities
- history_champions (insert-if-missing by season+team+trophy)
- org_development_report_archives
- player_rating_snapshots
- player_analytics_snapshots
- team_analytics_snapshots
- players.boost_tier (gold/silver/hof markers)

OVR baselines, trade logs, and game_record_baselines use their own transfer scripts.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_VERSION = 1
_SHEET_SEASON_RE = re.compile(r"sheet_season=([^\s|]+)", re.I)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    db_path = db_path.resolve()
    if readonly:
        return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60.0)
    return sqlite3.connect(str(db_path), timeout=60.0)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (name,),
        ).fetchone()
    )


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({name})")}


def _fhm_lookups(
    conn: sqlite3.Connection,
) -> tuple[dict[str, int], dict[int, str], dict[str, int], dict[int, str]]:
    player_id_by_fhm: dict[str, int] = {}
    player_fhm_by_id: dict[int, str] = {}
    if _has_table(conn, "players"):
        for pid, fhm in conn.execute(
            "SELECT id, fhm_player_id FROM players "
            "WHERE fhm_player_id IS NOT NULL AND TRIM(fhm_player_id) != ''"
        ):
            key = str(fhm).strip()
            player_id_by_fhm[key] = int(pid)
            player_fhm_by_id[int(pid)] = key
    team_id_by_fhm: dict[str, int] = {}
    team_fhm_by_id: dict[int, str] = {}
    if _has_table(conn, "teams"):
        for tid, fhm in conn.execute(
            "SELECT id, fhm_team_id FROM teams "
            "WHERE fhm_team_id IS NOT NULL AND TRIM(fhm_team_id) != ''"
        ):
            key = str(fhm).strip()
            team_id_by_fhm[key] = int(tid)
            team_fhm_by_id[int(tid)] = key
    return player_id_by_fhm, player_fhm_by_id, team_id_by_fhm, team_fhm_by_id


def _season_lookups(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[int, str], int | None]:
    """label/start_year -> season_id, id -> label, current season id."""
    by_label: dict[str, int] = {}
    label_by_id: dict[int, str] = {}
    current_id: int | None = None
    if not _has_table(conn, "seasons"):
        return by_label, label_by_id, current_id
    for sid, label, start_year, is_current in conn.execute(
        "SELECT id, label, start_year, is_current FROM seasons"
    ):
        sid_i = int(sid)
        lab = str(label or "").strip()
        if lab:
            by_label[lab] = sid_i
            label_by_id[sid_i] = lab
        if start_year is not None:
            by_label[str(int(start_year))] = sid_i
            # also common labels like 2000-01
        if is_current:
            current_id = sid_i
    return by_label, label_by_id, current_id


def _sheet_season_from_notes(notes: str | None) -> str | None:
    if not notes:
        return None
    m = _SHEET_SEASON_RE.search(str(notes))
    return m.group(1).strip() if m else None


def _resolve_season_id(
    *,
    season_label: str | None,
    notes: str | None,
    by_label: dict[str, int],
    current_id: int | None,
) -> int | None:
    sheet = _sheet_season_from_notes(notes)
    for candidate in (sheet, season_label):
        if candidate and str(candidate).strip() in by_label:
            return by_label[str(candidate).strip()]
        # try start year from YYYY-YY
        if candidate:
            m = re.match(r"^(\d{4})", str(candidate).strip())
            if m and m.group(1) in by_label:
                return by_label[m.group(1)]
    return current_id


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _export_record_stat_adjustments(conn: sqlite3.Connection, pf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "record_stat_adjustments"):
        return []
    cols = _table_columns(conn, "record_stat_adjustments")
    rows = conn.execute("SELECT * FROM record_stat_adjustments").fetchall()
    col_names = [r[1] for r in conn.execute("PRAGMA table_info(record_stat_adjustments)")]
    out: list[dict] = []
    for row in rows:
        d = dict(zip(col_names, row))
        pid = d.get("player_id")
        out.append(
            {
                "adj_type": d.get("adj_type"),
                "line_kind": d.get("line_kind"),
                "player_fhm_id": pf.get(int(pid)) if pid is not None else None,
                "season_year": d.get("season_year"),
                "team_fhm_id": d.get("team_fhm_id"),
                "career_source": d.get("career_source"),
                "overrides_json": d.get("overrides_json"),
                "notes": d.get("notes"),
            }
        )
    return out


def _export_team_honors(
    conn: sqlite3.Connection, tf: dict[int, str]
) -> tuple[list[dict], list[dict], list[dict]]:
    meta: list[dict] = []
    retired: list[dict] = []
    banners: list[dict] = []
    if _has_table(conn, "team_honors_meta"):
        for team_id, enabled in conn.execute(
            "SELECT team_id, retired_section_enabled FROM team_honors_meta"
        ):
            fhm = tf.get(int(team_id))
            if not fhm:
                continue
            meta.append({"team_fhm_id": fhm, "retired_section_enabled": bool(enabled)})
    if _has_table(conn, "team_retired_numbers"):
        col_names = [r[1] for r in conn.execute("PRAGMA table_info(team_retired_numbers)")]
        for row in conn.execute("SELECT * FROM team_retired_numbers"):
            d = dict(zip(col_names, row))
            fhm = tf.get(int(d["team_id"])) if d.get("team_id") is not None else None
            if not fhm:
                continue
            retired.append(
                {
                    "team_fhm_id": fhm,
                    "player_name": d.get("player_name"),
                    "jersey_number": d.get("jersey_number"),
                    "jersey_image_rel_path": d.get("jersey_image_rel_path"),
                    "number_color": d.get("number_color"),
                    "is_active": d.get("is_active"),
                    "sort_order": d.get("sort_order"),
                    "notes": d.get("notes"),
                }
            )
    if _has_table(conn, "team_victory_banners"):
        col_names = [r[1] for r in conn.execute("PRAGMA table_info(team_victory_banners)")]
        for row in conn.execute("SELECT * FROM team_victory_banners"):
            d = dict(zip(col_names, row))
            fhm = tf.get(int(d["team_id"])) if d.get("team_id") is not None else None
            if not fhm:
                continue
            banners.append(
                {
                    "team_fhm_id": fhm,
                    "title": d.get("title"),
                    "victory_number": d.get("victory_number"),
                    "banner_image_rel_path": d.get("banner_image_rel_path"),
                    "is_active": d.get("is_active"),
                    "sort_order": d.get("sort_order"),
                    "notes": d.get("notes"),
                }
            )
    return meta, retired, banners


def _export_hof(conn: sqlite3.Connection, pf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "hall_of_fame_members"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(hall_of_fame_members)")]
    has_source = "source" in set(names)
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM hall_of_fame_members"):
        d = dict(zip(names, row))
        fhm = pf.get(int(d["player_id"])) if d.get("player_id") is not None else None
        if not fhm:
            continue
        out.append(
            {
                "player_fhm_id": fhm,
                "member_kind": d.get("member_kind"),
                "inducted_year": d.get("inducted_year"),
                "sort_order": d.get("sort_order"),
                "source": d.get("source") if has_source else "csv",
            }
        )
    return out


def _export_history_awards(
    conn: sqlite3.Connection,
    pf: dict[int, str],
    tf: dict[int, str],
    label_by_id: dict[int, str],
) -> list[dict]:
    if not _has_table(conn, "history_awards"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(history_awards)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM history_awards"):
        d = dict(zip(names, row))
        sid = d.get("season_id")
        season_label = label_by_id.get(int(sid)) if sid is not None else None
        sheet = _sheet_season_from_notes(d.get("notes")) or season_label
        pid = d.get("player_id")
        tid = d.get("team_id")
        out.append(
            {
                "season_label": sheet,
                "award_name": d.get("award_name"),
                "player_fhm_id": pf.get(int(pid)) if pid is not None else None,
                "team_fhm_id": tf.get(int(tid)) if tid is not None else None,
                "staff_fhm_id": d.get("staff_fhm_id"),
                "notes": d.get("notes"),
                "source": d.get("source") or "csv",
            }
        )
    return out


def _export_history_all_stars(
    conn: sqlite3.Connection, pf: dict[int, str], tf: dict[int, str]
) -> list[dict]:
    if not _has_table(conn, "history_all_stars"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(history_all_stars)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM history_all_stars"):
        d = dict(zip(names, row))
        pid = d.get("player_id")
        tid = d.get("team_id")
        out.append(
            {
                "season_label": d.get("season_label"),
                "team_rank": d.get("team_rank"),
                "slot": d.get("slot"),
                "position": d.get("position"),
                "player_fhm_id": pf.get(int(pid)) if pid is not None else None,
                "team_fhm_id": tf.get(int(tid)) if tid is not None else None,
                "notes": d.get("notes"),
                "source": d.get("source") or "csv",
            }
        )
    return out


def _export_player_boost_tiers(conn: sqlite3.Connection) -> list[dict]:
    if not _has_table(conn, "players"):
        return []
    cols = _table_columns(conn, "players")
    if "boost_tier" not in cols:
        return []
    out: list[dict] = []
    for fhm, tier in conn.execute(
        """
        SELECT fhm_player_id, boost_tier FROM players
        WHERE fhm_player_id IS NOT NULL AND TRIM(fhm_player_id) != ''
          AND boost_tier IS NOT NULL AND TRIM(boost_tier) != ''
        """
    ):
        out.append({"player_fhm_id": str(fhm).strip(), "boost_tier": str(tier).strip()})
    return out


def _export_team_season_records(conn: sqlite3.Connection, tf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "team_season_records"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(team_season_records)")]
    skip = {"id", "updated_at", "updated_by_user_id"}
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM team_season_records"):
        d = dict(zip(names, row))
        # Preserve admin + import rows (csv rebuilt locally from template).
        src = str(d.get("source") or "csv")
        if src not in ("admin", "import"):
            continue
        tid = d.get("team_id")
        payload = {k: v for k, v in d.items() if k not in skip and k != "team_id"}
        payload["team_fhm_id"] = (
            d.get("team_fhm_id_csv")
            or (tf.get(int(tid)) if tid is not None else None)
        )
        out.append(payload)
    return out


def _export_franchise_identities(conn: sqlite3.Connection, tf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "franchise_team_identities"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(franchise_team_identities)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM franchise_team_identities"):
        d = dict(zip(names, row))
        tid = d.get("team_id")
        fhm = d.get("team_fhm_id") or (tf.get(int(tid)) if tid is not None else None)
        if not fhm:
            continue
        out.append(
            {
                "team_fhm_id": str(fhm).strip(),
                "display_name": d.get("display_name"),
                "abbreviation": d.get("abbreviation"),
                "logo_file": d.get("logo_file"),
                "start_year": d.get("start_year"),
                "end_year": d.get("end_year"),
                "status": d.get("status"),
                "notes": d.get("notes"),
            }
        )
    return out


def _export_history_champions(
    conn: sqlite3.Connection, tf: dict[int, str], label_by_id: dict[int, str]
) -> list[dict]:
    if not _has_table(conn, "history_champions"):
        return []
    out: list[dict] = []
    for sid, tid, trophy in conn.execute(
        "SELECT season_id, team_id, trophy FROM history_champions"
    ):
        fhm = tf.get(int(tid)) if tid is not None else None
        lab = label_by_id.get(int(sid)) if sid is not None else None
        if not fhm or not lab:
            continue
        out.append({"season_label": lab, "team_fhm_id": fhm, "trophy": trophy})
    return out


def _export_org_archives(conn: sqlite3.Connection, tf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "org_development_report_archives"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(org_development_report_archives)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM org_development_report_archives"):
        d = dict(zip(names, row))
        fhm = tf.get(int(d["team_id"])) if d.get("team_id") is not None else None
        if not fhm:
            continue
        out.append(
            {
                "team_fhm_id": fhm,
                "league_slug": d.get("league_slug"),
                "timeline_key": d.get("timeline_key"),
                "timeline_season_start_year": d.get("timeline_season_start_year"),
                "timeline_calendar_year": d.get("timeline_calendar_year"),
                "timeline_calendar_month": d.get("timeline_calendar_month"),
                "label": d.get("label"),
                "report_json": d.get("report_json"),
                "archived_at": str(d.get("archived_at") or ""),
            }
        )
    return out


def _export_rating_snapshots(conn: sqlite3.Connection, pf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "player_rating_snapshots"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(player_rating_snapshots)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM player_rating_snapshots"):
        d = dict(zip(names, row))
        fhm = pf.get(int(d["player_id"])) if d.get("player_id") is not None else None
        if not fhm:
            continue
        out.append(
            {
                "player_fhm_id": fhm,
                "league_slug": d.get("league_slug"),
                "snapshot_at": str(d.get("snapshot_at") or ""),
                "ratings_json": d.get("ratings_json"),
                "ability": d.get("ability"),
                "potential": d.get("potential"),
                "overall_score": d.get("overall_score"),
                "timeline_season_start_year": d.get("timeline_season_start_year"),
                "timeline_calendar_year": d.get("timeline_calendar_year"),
                "timeline_calendar_month": d.get("timeline_calendar_month"),
            }
        )
    return out


def _export_player_analytics_snapshots(conn: sqlite3.Connection, pf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "player_analytics_snapshots"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(player_analytics_snapshots)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM player_analytics_snapshots"):
        d = dict(zip(names, row))
        fhm = pf.get(int(d["player_id"])) if d.get("player_id") is not None else None
        if not fhm:
            continue
        out.append(
            {
                "player_fhm_id": fhm,
                "league_slug": d.get("league_slug"),
                "season_year": d.get("season_year"),
                "stat_segment": d.get("stat_segment"),
                "is_goalie": d.get("is_goalie"),
                "is_rollover": d.get("is_rollover"),
                "snapshot_at": str(d.get("snapshot_at") or ""),
                "war_pct": d.get("war_pct"),
                "gp": d.get("gp"),
                "metrics_json": d.get("metrics_json"),
                "percentiles_json": d.get("percentiles_json"),
            }
        )
    return out


def _export_team_analytics_snapshots(conn: sqlite3.Connection, tf: dict[int, str]) -> list[dict]:
    if not _has_table(conn, "team_analytics_snapshots"):
        return []
    names = [r[1] for r in conn.execute("PRAGMA table_info(team_analytics_snapshots)")]
    out: list[dict] = []
    for row in conn.execute("SELECT * FROM team_analytics_snapshots"):
        d = dict(zip(names, row))
        fhm = tf.get(int(d["team_id"])) if d.get("team_id") is not None else None
        if not fhm:
            continue
        out.append(
            {
                "team_fhm_id": fhm,
                "league_slug": d.get("league_slug"),
                "season_year": d.get("season_year"),
                "stat_segment": d.get("stat_segment"),
                "is_rollover": d.get("is_rollover"),
                "snapshot_at": str(d.get("snapshot_at") or ""),
                "metrics_json": d.get("metrics_json"),
            }
        )
    return out


def export_league_editorial_json(db_path: Path, out_path: Path) -> dict[str, int]:
    """Write a multi-table JSON bundle. Returns counts per section."""
    conn = _connect(db_path, readonly=True)
    try:
        _pib, pf, _tib, tf = _fhm_lookups(conn)
        _by_label, label_by_id, _current = _season_lookups(conn)
        meta, retired, banners = _export_team_honors(conn, tf)
        bundle: dict[str, Any] = {
            "version": BUNDLE_VERSION,
            "exported_at": _utc_now_iso(),
            "record_stat_adjustments": _export_record_stat_adjustments(conn, pf),
            "team_honors_meta": meta,
            "team_retired_numbers": retired,
            "team_victory_banners": banners,
            "hall_of_fame_members": _export_hof(conn, pf),
            "history_awards": _export_history_awards(conn, pf, tf, label_by_id),
            "history_all_stars": _export_history_all_stars(conn, pf, tf),
            "team_season_records": _export_team_season_records(conn, tf),
            "franchise_team_identities": _export_franchise_identities(conn, tf),
            "history_champions": _export_history_champions(conn, tf, label_by_id),
            "org_development_report_archives": _export_org_archives(conn, tf),
            "player_rating_snapshots": _export_rating_snapshots(conn, pf),
            "player_analytics_snapshots": _export_player_analytics_snapshots(conn, pf),
            "team_analytics_snapshots": _export_team_analytics_snapshots(conn, tf),
            "player_boost_tiers": _export_player_boost_tiers(conn),
        }
    finally:
        conn.close()
    row_counts = {
        k: (len(v) if isinstance(v, list) else 0)
        for k, v in bundle.items()
        if k not in ("version", "exported_at")
    }
    bundle["row_counts"] = row_counts
    bundle["total_rows"] = int(sum(row_counts.values()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return row_counts


# ---------------------------------------------------------------------------
# Import / merge
# ---------------------------------------------------------------------------


def _ensure_editorial_tables(conn: sqlite3.Connection) -> None:
    """Create tables if missing (minimal DDL matching models)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS record_stat_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adj_type VARCHAR(16) NOT NULL,
            line_kind VARCHAR(24) NOT NULL DEFAULT 'skater_career',
            player_id INTEGER,
            season_year INTEGER,
            team_fhm_id VARCHAR(64),
            career_source VARCHAR(24),
            overrides_json TEXT,
            notes TEXT,
            updated_at DATETIME NOT NULL,
            updated_by_user_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS team_honors_meta (
            team_id INTEGER NOT NULL PRIMARY KEY,
            retired_section_enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS team_retired_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            player_name VARCHAR(200) NOT NULL,
            jersey_number INTEGER NOT NULL,
            jersey_image_rel_path VARCHAR(500),
            number_color VARCHAR(16),
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE (team_id, jersey_number)
        );
        CREATE TABLE IF NOT EXISTS team_victory_banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL DEFAULT '',
            victory_number INTEGER NOT NULL,
            banner_image_rel_path VARCHAR(500),
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE (team_id, victory_number)
        );
        CREATE TABLE IF NOT EXISTS hall_of_fame_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL UNIQUE,
            member_kind VARCHAR(16) NOT NULL,
            inducted_year INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source VARCHAR(16) NOT NULL DEFAULT 'csv',
            updated_at DATETIME NOT NULL,
            updated_by_user_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS history_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            award_name VARCHAR(160) NOT NULL,
            player_id INTEGER,
            team_id INTEGER,
            staff_fhm_id VARCHAR(64),
            notes TEXT,
            source VARCHAR(16) NOT NULL DEFAULT 'csv',
            updated_at DATETIME NOT NULL,
            updated_by_user_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS history_all_stars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            season_label VARCHAR(16) NOT NULL DEFAULT '',
            team_rank INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            position VARCHAR(32) NOT NULL,
            player_id INTEGER,
            team_id INTEGER,
            notes TEXT,
            source VARCHAR(16) NOT NULL DEFAULT 'csv',
            updated_at DATETIME NOT NULL,
            updated_by_user_id INTEGER,
            UNIQUE (season_label, team_rank, slot)
        );
        CREATE TABLE IF NOT EXISTS franchise_team_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER,
            team_fhm_id VARCHAR(64),
            display_name VARCHAR(200) NOT NULL,
            abbreviation VARCHAR(16),
            logo_file VARCHAR(500),
            start_year INTEGER NOT NULL,
            end_year INTEGER,
            status VARCHAR(32) NOT NULL DEFAULT 'historical',
            notes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS history_champions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            trophy VARCHAR(120)
        );
        CREATE TABLE IF NOT EXISTS org_development_report_archives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            league_slug VARCHAR(64) NOT NULL,
            timeline_key VARCHAR(16) NOT NULL,
            timeline_season_start_year INTEGER NOT NULL,
            timeline_calendar_year INTEGER NOT NULL,
            timeline_calendar_month INTEGER NOT NULL,
            label VARCHAR(160) NOT NULL,
            report_json TEXT NOT NULL,
            archived_at DATETIME NOT NULL,
            UNIQUE (team_id, timeline_key)
        );
        CREATE TABLE IF NOT EXISTS player_rating_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            league_slug VARCHAR(64) NOT NULL,
            snapshot_at DATETIME NOT NULL,
            ratings_json TEXT NOT NULL,
            ability FLOAT,
            potential FLOAT,
            overall_score INTEGER,
            timeline_season_start_year INTEGER,
            timeline_calendar_year INTEGER,
            timeline_calendar_month INTEGER
        );
        CREATE TABLE IF NOT EXISTS player_analytics_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            league_slug VARCHAR(64) NOT NULL,
            season_year INTEGER NOT NULL,
            stat_segment VARCHAR(8) NOT NULL,
            is_goalie BOOLEAN NOT NULL,
            is_rollover BOOLEAN NOT NULL,
            snapshot_at DATETIME NOT NULL,
            war_pct INTEGER,
            gp INTEGER,
            metrics_json TEXT NOT NULL,
            percentiles_json TEXT
        );
        CREATE TABLE IF NOT EXISTS team_analytics_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            league_slug VARCHAR(64) NOT NULL,
            season_year INTEGER NOT NULL,
            stat_segment VARCHAR(8) NOT NULL,
            is_rollover BOOLEAN NOT NULL,
            snapshot_at DATETIME NOT NULL,
            metrics_json TEXT NOT NULL
        );
        """
    )


def _adj_key(row: dict) -> tuple:
    return (
        str(row.get("player_fhm_id") or ""),
        str(row.get("season_year") if row.get("season_year") is not None else ""),
        str(row.get("team_fhm_id") or ""),
        str(row.get("career_source") or ""),
        str(row.get("line_kind") or ""),
        str(row.get("adj_type") or ""),
    )


def _import_record_stat_adjustments(
    conn: sqlite3.Connection, rows: list[dict], pib: dict[str, int], now: str
) -> int:
    if not rows:
        return 0
    existing_keys: set[tuple] = set()
    if _has_table(conn, "record_stat_adjustments"):
        names = [r[1] for r in conn.execute("PRAGMA table_info(record_stat_adjustments)")]
        pf_by_id = {v: k for k, v in pib.items()}
        for row in conn.execute("SELECT * FROM record_stat_adjustments"):
            d = dict(zip(names, row))
            pid = d.get("player_id")
            existing_keys.add(
                _adj_key(
                    {
                        "player_fhm_id": pf_by_id.get(int(pid)) if pid is not None else None,
                        "season_year": d.get("season_year"),
                        "team_fhm_id": d.get("team_fhm_id"),
                        "career_source": d.get("career_source"),
                        "line_kind": d.get("line_kind"),
                        "adj_type": d.get("adj_type"),
                    }
                )
            )
    written = 0
    for item in rows:
        key = _adj_key(item)
        if key in existing_keys:
            # Live wins: delete local match then insert live
            fhm = str(item.get("player_fhm_id") or "").strip()
            pid = pib.get(fhm) if fhm else None
            if pid is not None:
                conn.execute(
                    "DELETE FROM record_stat_adjustments WHERE player_id=? AND "
                    "IFNULL(season_year,-1)=IFNULL(?, -1) AND IFNULL(team_fhm_id,'')=IFNULL(?, '') "
                    "AND IFNULL(career_source,'')=IFNULL(?, '') AND line_kind=? AND adj_type=?",
                    (
                        pid,
                        item.get("season_year"),
                        item.get("team_fhm_id"),
                        item.get("career_source"),
                        item.get("line_kind"),
                        item.get("adj_type"),
                    ),
                )
        fhm = str(item.get("player_fhm_id") or "").strip()
        pid = pib.get(fhm) if fhm else None
        conn.execute(
            """
            INSERT INTO record_stat_adjustments (
                adj_type, line_kind, player_id, season_year, team_fhm_id,
                career_source, overrides_json, notes, updated_at, updated_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                item.get("adj_type"),
                item.get("line_kind") or "skater_career",
                pid,
                item.get("season_year"),
                item.get("team_fhm_id"),
                item.get("career_source"),
                item.get("overrides_json"),
                item.get("notes"),
                now,
            ),
        )
        existing_keys.add(key)
        written += 1
    return written


def _import_team_honors(
    conn: sqlite3.Connection,
    meta: list[dict],
    retired: list[dict],
    banners: list[dict],
    tib: dict[str, int],
    now: str,
) -> int:
    n = 0
    for item in meta:
        tid = tib.get(str(item.get("team_fhm_id") or "").strip())
        if tid is None:
            continue
        conn.execute(
            """
            INSERT INTO team_honors_meta (team_id, retired_section_enabled)
            VALUES (?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                retired_section_enabled = excluded.retired_section_enabled
            """,
            (tid, 1 if item.get("retired_section_enabled") else 0),
        )
        n += 1
    for item in retired:
        tid = tib.get(str(item.get("team_fhm_id") or "").strip())
        if tid is None:
            continue
        jersey = item.get("jersey_number")
        if jersey is None:
            continue
        conn.execute(
            """
            INSERT INTO team_retired_numbers (
                team_id, player_name, jersey_number, jersey_image_rel_path, number_color,
                is_active, sort_order, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, jersey_number) DO UPDATE SET
                player_name=excluded.player_name,
                jersey_image_rel_path=excluded.jersey_image_rel_path,
                number_color=excluded.number_color,
                is_active=excluded.is_active,
                sort_order=excluded.sort_order,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                tid,
                item.get("player_name") or "",
                int(jersey),
                item.get("jersey_image_rel_path"),
                item.get("number_color"),
                1 if item.get("is_active", True) else 0,
                int(item.get("sort_order") or 0),
                item.get("notes") or "",
                now,
                now,
            ),
        )
        n += 1
    for item in banners:
        tid = tib.get(str(item.get("team_fhm_id") or "").strip())
        if tid is None:
            continue
        vic = item.get("victory_number")
        if vic is None:
            continue
        conn.execute(
            """
            INSERT INTO team_victory_banners (
                team_id, title, victory_number, banner_image_rel_path,
                is_active, sort_order, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, victory_number) DO UPDATE SET
                title=excluded.title,
                banner_image_rel_path=excluded.banner_image_rel_path,
                is_active=excluded.is_active,
                sort_order=excluded.sort_order,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                tid,
                item.get("title") or "",
                int(vic),
                item.get("banner_image_rel_path"),
                1 if item.get("is_active", True) else 0,
                int(item.get("sort_order") or 0),
                item.get("notes") or "",
                now,
                now,
            ),
        )
        n += 1
    return n


def _import_hof(conn: sqlite3.Connection, rows: list[dict], pib: dict[str, int], now: str) -> int:
    """Gap-fill HoF from live: insert missing; overwrite only when live is admin."""
    n = 0
    for item in rows:
        fhm = str(item.get("player_fhm_id") or "").strip()
        pid = pib.get(fhm)
        if pid is None:
            continue
        source = str(item.get("source") or "csv")
        existing = conn.execute(
            "SELECT id, source FROM hall_of_fame_members WHERE player_id=?", (pid,)
        ).fetchone()
        member_kind = item.get("member_kind") or "skater"
        inducted_year = int(item.get("inducted_year") or 0)
        sort_order = int(item.get("sort_order") or 0)
        if existing is None:
            conn.execute(
                """
                INSERT INTO hall_of_fame_members (
                    player_id, member_kind, inducted_year, sort_order, source, updated_at, updated_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (pid, member_kind, inducted_year, sort_order, source, now),
            )
            n += 1
            continue
        # Local row exists: only live admin may overwrite (including local admin).
        if source != "admin":
            continue
        conn.execute(
            """
            UPDATE hall_of_fame_members SET
                member_kind=?, inducted_year=?, sort_order=?, source=?, updated_at=?
            WHERE id=?
            """,
            (member_kind, inducted_year, sort_order, source, now, int(existing[0])),
        )
        n += 1
    return n


def _award_key(row: dict) -> tuple:
    return (
        str(row.get("season_label") or ""),
        str(row.get("award_name") or ""),
        str(row.get("player_fhm_id") or ""),
        str(row.get("team_fhm_id") or ""),
        str(row.get("staff_fhm_id") or ""),
    )


def _import_history_awards(
    conn: sqlite3.Connection,
    rows: list[dict],
    pib: dict[str, int],
    tib: dict[str, int],
    by_label: dict[str, int],
    current_id: int | None,
    now: str,
) -> int:
    """Merge live awards: admin force-upsert; other sources insert-if-missing only."""
    admin_rows = [r for r in rows if str(r.get("source") or "") == "admin"]
    other_rows = [r for r in rows if str(r.get("source") or "") != "admin"]
    n = 0

    def _find_match(season_id: int, item: dict, pid: int | None):
        if pid is not None or item.get("staff_fhm_id"):
            return conn.execute(
                """
                SELECT id, source, notes FROM history_awards
                WHERE season_id=? AND award_name=?
                  AND IFNULL(player_id,-1)=IFNULL(?, -1)
                  AND IFNULL(staff_fhm_id,'')=IFNULL(?, '')
                LIMIT 1
                """,
                (season_id, item.get("award_name"), pid, item.get("staff_fhm_id")),
            ).fetchone()
        # Team-only / name-only awards: match on season + award + team.
        tid = tib.get(str(item.get("team_fhm_id") or "").strip()) if item.get("team_fhm_id") else None
        return conn.execute(
            """
            SELECT id, source, notes FROM history_awards
            WHERE season_id=? AND award_name=?
              AND player_id IS NULL
              AND IFNULL(staff_fhm_id,'')=IFNULL(?, '')
              AND IFNULL(team_id,-1)=IFNULL(?, -1)
            LIMIT 1
            """,
            (season_id, item.get("award_name"), item.get("staff_fhm_id"), tid),
        ).fetchone()

    def _upsert(item: dict, *, force: bool, gap_fill_only: bool) -> None:
        nonlocal n
        season_id = _resolve_season_id(
            season_label=item.get("season_label"),
            notes=item.get("notes"),
            by_label=by_label,
            current_id=current_id,
        )
        if season_id is None:
            return
        pid = pib.get(str(item.get("player_fhm_id") or "").strip()) if item.get("player_fhm_id") else None
        tid = tib.get(str(item.get("team_fhm_id") or "").strip()) if item.get("team_fhm_id") else None
        source = str(item.get("source") or "csv")
        match = _find_match(season_id, item, pid)
        if match is not None:
            if gap_fill_only:
                return
            local_src = str(match[1] or "csv")
            if local_src == "admin" and source != "admin" and not force:
                return
            conn.execute(
                """
                UPDATE history_awards SET
                    player_id=?, team_id=?, staff_fhm_id=?, notes=?, source=?, updated_at=?
                WHERE id=?
                """,
                (
                    pid,
                    tid,
                    item.get("staff_fhm_id"),
                    item.get("notes"),
                    source,
                    now,
                    int(match[0]),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO history_awards (
                    season_id, award_name, player_id, team_id, staff_fhm_id,
                    notes, source, updated_at, updated_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    season_id,
                    item.get("award_name"),
                    pid,
                    tid,
                    item.get("staff_fhm_id"),
                    item.get("notes"),
                    source,
                    now,
                ),
            )
        n += 1

    for item in admin_rows:
        _upsert(item, force=True, gap_fill_only=False)
    for item in other_rows:
        _upsert(item, force=False, gap_fill_only=True)
    return n


def _import_history_all_stars(
    conn: sqlite3.Connection,
    rows: list[dict],
    pib: dict[str, int],
    tib: dict[str, int],
    by_label: dict[str, int],
    current_id: int | None,
    now: str,
) -> int:
    """Merge live all-stars: admin force-upsert; CSV/other insert-if-missing only."""
    n = 0
    for item in rows:
        source = str(item.get("source") or "csv")
        label = str(item.get("season_label") or "").strip()
        if not label:
            continue
        season_id = _resolve_season_id(
            season_label=label, notes=item.get("notes"), by_label=by_label, current_id=current_id
        )
        if season_id is None:
            continue
        team_rank = int(item.get("team_rank") or 0)
        slot = int(item.get("slot") or 0)
        existing = conn.execute(
            """
            SELECT id, source FROM history_all_stars
            WHERE season_label=? AND team_rank=? AND slot=?
            LIMIT 1
            """,
            (label, team_rank, slot),
        ).fetchone()
        pid = pib.get(str(item.get("player_fhm_id") or "").strip()) if item.get("player_fhm_id") else None
        tid = tib.get(str(item.get("team_fhm_id") or "").strip()) if item.get("team_fhm_id") else None
        if existing is not None and source != "admin":
            # Gap-fill only: local already has this slot.
            continue
        if existing is None:
            conn.execute(
                """
                INSERT INTO history_all_stars (
                    season_id, season_label, team_rank, slot, position,
                    player_id, team_id, notes, source, updated_at, updated_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    season_id,
                    label,
                    team_rank,
                    slot,
                    item.get("position") or "",
                    pid,
                    tid,
                    item.get("notes"),
                    source,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE history_all_stars SET
                    season_id=?, position=?, player_id=?, team_id=?, notes=?, source=?, updated_at=?
                WHERE id=?
                """,
                (
                    season_id,
                    item.get("position") or "",
                    pid,
                    tid,
                    item.get("notes"),
                    source,
                    now,
                    int(existing[0]),
                ),
            )
        n += 1
    return n


def _import_team_season_records(
    conn: sqlite3.Connection, rows: list[dict], tib: dict[str, int], now: str
) -> int:
    if not rows or not _has_table(conn, "team_season_records"):
        return 0
    names = [r[1] for r in conn.execute("PRAGMA table_info(team_season_records)")]
    writable = [c for c in names if c not in ("id",)]
    n = 0
    for item in rows:
        src = str(item.get("source") or "")
        if src not in ("admin", "import"):
            continue
        fhm = str(item.get("team_fhm_id") or item.get("team_fhm_id_csv") or "").strip()
        tid = tib.get(fhm) if fhm else None
        year = item.get("season_year_label")
        override = item.get("team_name_override")
        if year is None:
            continue
        # Prefer live admin over everything; live import over local csv only.
        if tid is not None:
            existing = conn.execute(
                """
                SELECT id, source FROM team_season_records
                WHERE season_year_label=? AND team_id=?
                  AND IFNULL(team_name_override,'')=IFNULL(?, '')
                LIMIT 1
                """,
                (year, tid, override),
            ).fetchone()
            if existing is not None:
                local_src = str(existing[1] or "csv")
                if local_src == "admin" and src != "admin":
                    continue
                conn.execute("DELETE FROM team_season_records WHERE id=?", (int(existing[0]),))
        cols = []
        vals = []
        for c in writable:
            if c == "team_id":
                cols.append(c)
                vals.append(tid)
            elif c == "team_fhm_id_csv":
                cols.append(c)
                vals.append(item.get("team_fhm_id_csv") or fhm or None)
            elif c == "updated_at":
                cols.append(c)
                vals.append(now)
            elif c == "updated_by_user_id":
                cols.append(c)
                vals.append(None)
            elif c == "source":
                cols.append(c)
                vals.append(src)
            elif c in item:
                cols.append(c)
                vals.append(item.get(c))
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO team_season_records ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        n += 1
    return n


def _import_player_boost_tiers(
    conn: sqlite3.Connection, rows: list[dict], pib: dict[str, int]
) -> int:
    if not rows or not _has_table(conn, "players"):
        return 0
    cols = _table_columns(conn, "players")
    if "boost_tier" not in cols:
        return 0
    n = 0
    for item in rows:
        fhm = str(item.get("player_fhm_id") or "").strip()
        tier = str(item.get("boost_tier") or "").strip()
        pid = pib.get(fhm)
        if pid is None or not tier:
            continue
        conn.execute("UPDATE players SET boost_tier=? WHERE id=?", (tier, pid))
        n += 1
    return n


def _import_franchise_identities(
    conn: sqlite3.Connection, rows: list[dict], tib: dict[str, int]
) -> int:
    n = 0
    for item in rows:
        fhm = str(item.get("team_fhm_id") or "").strip()
        if not fhm:
            continue
        tid = tib.get(fhm)
        name = item.get("display_name")
        start = item.get("start_year")
        end = item.get("end_year")
        if not name or start is None:
            continue
        existing = conn.execute(
            """
            SELECT id FROM franchise_team_identities
            WHERE IFNULL(team_fhm_id,'')=? AND display_name=? AND start_year=?
              AND IFNULL(end_year,-1)=IFNULL(?, -1)
            LIMIT 1
            """,
            (fhm, name, int(start), end),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE franchise_team_identities SET
                    team_id=COALESCE(?, team_id),
                    abbreviation=?, logo_file=?, status=?, notes=?
                WHERE id=?
                """,
                (
                    tid,
                    item.get("abbreviation"),
                    item.get("logo_file"),
                    item.get("status") or "historical",
                    item.get("notes") or "",
                    int(existing[0]),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO franchise_team_identities (
                    team_id, team_fhm_id, display_name, abbreviation, logo_file,
                    start_year, end_year, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    fhm,
                    name,
                    item.get("abbreviation"),
                    item.get("logo_file"),
                    int(start),
                    end,
                    item.get("status") or "historical",
                    item.get("notes") or "",
                ),
            )
        n += 1
    return n


def _import_history_champions(
    conn: sqlite3.Connection,
    rows: list[dict],
    tib: dict[str, int],
    by_label: dict[str, int],
    current_id: int | None,
) -> int:
    n = 0
    for item in rows:
        lab = str(item.get("season_label") or "").strip()
        fhm = str(item.get("team_fhm_id") or "").strip()
        tid = tib.get(fhm)
        season_id = _resolve_season_id(
            season_label=lab, notes=None, by_label=by_label, current_id=current_id
        )
        if tid is None or season_id is None:
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM history_champions
            WHERE season_id=? AND team_id=? AND IFNULL(trophy,'')=IFNULL(?, '')
            LIMIT 1
            """,
            (season_id, tid, item.get("trophy")),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO history_champions (season_id, team_id, trophy) VALUES (?, ?, ?)",
            (season_id, tid, item.get("trophy")),
        )
        n += 1
    return n


def _import_org_archives(
    conn: sqlite3.Connection, rows: list[dict], tib: dict[str, int]
) -> int:
    n = 0
    for item in rows:
        tid = tib.get(str(item.get("team_fhm_id") or "").strip())
        key = item.get("timeline_key")
        if tid is None or not key:
            continue
        conn.execute(
            """
            INSERT INTO org_development_report_archives (
                team_id, league_slug, timeline_key, timeline_season_start_year,
                timeline_calendar_year, timeline_calendar_month, label, report_json, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, timeline_key) DO UPDATE SET
                league_slug=excluded.league_slug,
                timeline_season_start_year=excluded.timeline_season_start_year,
                timeline_calendar_year=excluded.timeline_calendar_year,
                timeline_calendar_month=excluded.timeline_calendar_month,
                label=excluded.label,
                report_json=excluded.report_json,
                archived_at=excluded.archived_at
            """,
            (
                tid,
                item.get("league_slug") or "",
                key,
                int(item.get("timeline_season_start_year") or 0),
                int(item.get("timeline_calendar_year") or 0),
                int(item.get("timeline_calendar_month") or 0),
                item.get("label") or "",
                item.get("report_json") or "{}",
                item.get("archived_at") or _utc_now_iso(),
            ),
        )
        n += 1
    return n


def _import_rating_snapshots(
    conn: sqlite3.Connection, rows: list[dict], pib: dict[str, int]
) -> int:
    n = 0
    for item in rows:
        pid = pib.get(str(item.get("player_fhm_id") or "").strip())
        snap = item.get("snapshot_at")
        if pid is None or not snap:
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM player_rating_snapshots
            WHERE player_id=? AND snapshot_at=? LIMIT 1
            """,
            (pid, snap),
        ).fetchone()
        if exists:
            continue
        # Also skip if same timeline month already present
        ty = item.get("timeline_calendar_year")
        tm = item.get("timeline_calendar_month")
        if ty is not None and tm is not None:
            exists_month = conn.execute(
                """
                SELECT 1 FROM player_rating_snapshots
                WHERE player_id=? AND timeline_calendar_year=? AND timeline_calendar_month=?
                LIMIT 1
                """,
                (pid, ty, tm),
            ).fetchone()
            if exists_month:
                continue
        conn.execute(
            """
            INSERT INTO player_rating_snapshots (
                player_id, league_slug, snapshot_at, ratings_json, ability, potential,
                overall_score, timeline_season_start_year, timeline_calendar_year,
                timeline_calendar_month
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                item.get("league_slug") or "",
                snap,
                item.get("ratings_json") or "{}",
                item.get("ability"),
                item.get("potential"),
                item.get("overall_score"),
                item.get("timeline_season_start_year"),
                ty,
                tm,
            ),
        )
        n += 1
    return n


def _import_player_analytics_snapshots(
    conn: sqlite3.Connection, rows: list[dict], pib: dict[str, int]
) -> int:
    n = 0
    for item in rows:
        pid = pib.get(str(item.get("player_fhm_id") or "").strip())
        snap = item.get("snapshot_at")
        if pid is None or not snap:
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM player_analytics_snapshots
            WHERE player_id=? AND snapshot_at=? AND stat_segment=? AND is_goalie=?
            LIMIT 1
            """,
            (
                pid,
                snap,
                item.get("stat_segment") or "rs",
                1 if item.get("is_goalie") else 0,
            ),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO player_analytics_snapshots (
                player_id, league_slug, season_year, stat_segment, is_goalie, is_rollover,
                snapshot_at, war_pct, gp, metrics_json, percentiles_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                item.get("league_slug") or "",
                int(item.get("season_year") or 0),
                item.get("stat_segment") or "rs",
                1 if item.get("is_goalie") else 0,
                1 if item.get("is_rollover") else 0,
                snap,
                item.get("war_pct"),
                item.get("gp"),
                item.get("metrics_json") or "{}",
                item.get("percentiles_json"),
            ),
        )
        n += 1
    return n


def _import_team_analytics_snapshots(
    conn: sqlite3.Connection, rows: list[dict], tib: dict[str, int]
) -> int:
    n = 0
    for item in rows:
        tid = tib.get(str(item.get("team_fhm_id") or "").strip())
        snap = item.get("snapshot_at")
        if tid is None or not snap:
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM team_analytics_snapshots
            WHERE team_id=? AND snapshot_at=? AND stat_segment=?
            LIMIT 1
            """,
            (tid, snap, item.get("stat_segment") or "rs"),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO team_analytics_snapshots (
                team_id, league_slug, season_year, stat_segment, is_rollover,
                snapshot_at, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                item.get("league_slug") or "",
                int(item.get("season_year") or 0),
                item.get("stat_segment") or "rs",
                1 if item.get("is_rollover") else 0,
                snap,
                item.get("metrics_json") or "{}",
            ),
        )
        n += 1
    return n


def import_league_editorial_json(db_path: Path, in_path: Path) -> dict[str, int]:
    """Merge live editorial JSON into local league DB. Returns write counts."""
    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in {in_path}")
    now = _utc_now_iso()
    conn = _connect(db_path, readonly=False)
    counts: dict[str, int] = {}
    try:
        _ensure_editorial_tables(conn)
        pib, _pf, tib, _tf = _fhm_lookups(conn)
        by_label, _label_by_id, current_id = _season_lookups(conn)

        counts["record_stat_adjustments"] = _import_record_stat_adjustments(
            conn, list(raw.get("record_stat_adjustments") or []), pib, now
        )
        counts["team_honors"] = _import_team_honors(
            conn,
            list(raw.get("team_honors_meta") or []),
            list(raw.get("team_retired_numbers") or []),
            list(raw.get("team_victory_banners") or []),
            tib,
            now,
        )
        counts["hall_of_fame_members"] = _import_hof(
            conn, list(raw.get("hall_of_fame_members") or []), pib, now
        )
        counts["history_awards"] = _import_history_awards(
            conn,
            list(raw.get("history_awards") or []),
            pib,
            tib,
            by_label,
            current_id,
            now,
        )
        counts["history_all_stars"] = _import_history_all_stars(
            conn,
            list(raw.get("history_all_stars") or []),
            pib,
            tib,
            by_label,
            current_id,
            now,
        )
        counts["team_season_records"] = _import_team_season_records(
            conn, list(raw.get("team_season_records") or []), tib, now
        )
        counts["franchise_team_identities"] = _import_franchise_identities(
            conn, list(raw.get("franchise_team_identities") or []), tib
        )
        counts["history_champions"] = _import_history_champions(
            conn, list(raw.get("history_champions") or []), tib, by_label, current_id
        )
        counts["org_development_report_archives"] = _import_org_archives(
            conn, list(raw.get("org_development_report_archives") or []), tib
        )
        counts["player_rating_snapshots"] = _import_rating_snapshots(
            conn, list(raw.get("player_rating_snapshots") or []), pib
        )
        counts["player_analytics_snapshots"] = _import_player_analytics_snapshots(
            conn, list(raw.get("player_analytics_snapshots") or []), pib
        )
        counts["team_analytics_snapshots"] = _import_team_analytics_snapshots(
            conn, list(raw.get("team_analytics_snapshots") or []), tib
        )
        counts["player_boost_tiers"] = _import_player_boost_tiers(
            conn, list(raw.get("player_boost_tiers") or []), pib
        )
        conn.commit()
    finally:
        conn.close()
    return counts


def _resolve_db_path(slug: str | None, db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path.resolve()
    if not slug:
        raise SystemExit("Provide --slug or --db-path.")
    from app.config import resolve_league_sqlite_path

    return resolve_league_sqlite_path(slug).resolve()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export league editorial bundle to JSON.")
    p_export.add_argument("--slug", help="League slug (e.g. bowl-cap).")
    p_export.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_export.add_argument("--out", type=Path, required=True, help="Output JSON path.")

    p_import = sub.add_parser("import", help="Merge live editorial JSON into a league DB.")
    p_import.add_argument("--slug", help="League slug (e.g. bowl-cap).")
    p_import.add_argument("--db-path", type=Path, help="SQLite file (overrides --slug).")
    p_import.add_argument("--in", dest="in_path", type=Path, required=True, help="Input JSON path.")

    args = p.parse_args()
    if args.command == "export":
        db_path = _resolve_db_path(args.slug, args.db_path)
        counts = export_league_editorial_json(db_path, args.out)
        total = sum(counts.values())
        print(
            f"export_league_editorial: {db_path.name} -> {args.out} ({total} rows total) {counts}"
        )
        return 0
    if args.command == "import":
        db_path = _resolve_db_path(args.slug, args.db_path)
        counts = import_league_editorial_json(db_path, args.in_path)
        total = sum(counts.values())
        print(
            f"import_league_editorial: {args.in_path.name} -> {db_path.name} "
            f"({total} written) {counts}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
