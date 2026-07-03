"""SQLite FTS5 helpers and post-migration setup."""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, OperationalError


def migrate_team_season_aggregates_sqlite(engine: Engine) -> None:
    """Rebuild team_season_aggregates when an older DB lacks stat_segment / new unique key."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_season_aggregates'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(team_season_aggregates)"))}
        if "stat_segment" in cols:
            return
        conn.execute(text("ALTER TABLE team_season_aggregates RENAME TO team_season_aggregates_old"))
        conn.execute(
            text(
                """
                CREATE TABLE team_season_aggregates (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    season_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    stat_segment VARCHAR(8) NOT NULL,
                    shots_for INTEGER,
                    shots_against INTEGER,
                    faceoff_pct FLOAT,
                    blocked_shots INTEGER,
                    hits INTEGER,
                    takeaways INTEGER,
                    giveaways INTEGER,
                    pp_chances INTEGER,
                    pp_goals INTEGER,
                    pk_goals_against INTEGER,
                    sh_chances INTEGER,
                    sh_goals INTEGER,
                    pim_per_game FLOAT,
                    attendance_home INTEGER,
                    attendance_away INTEGER,
                    sellouts_home INTEGER,
                    sellouts_away INTEGER,
                    capacity_use_pct FLOAT,
                    FOREIGN KEY(season_id) REFERENCES seasons (id),
                    FOREIGN KEY(team_id) REFERENCES teams (id),
                    CONSTRAINT uq_team_season_agg_seg UNIQUE (season_id, team_id, stat_segment)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO team_season_aggregates (
                    id, season_id, team_id, stat_segment,
                    shots_for, shots_against, faceoff_pct, blocked_shots, hits, takeaways, giveaways,
                    pp_chances, pp_goals, pk_goals_against, sh_chances, sh_goals, pim_per_game,
                    attendance_home, attendance_away, sellouts_home, sellouts_away, capacity_use_pct
                )
                SELECT
                    id, season_id, team_id, 'rs',
                    shots_for, shots_against, faceoff_pct, blocked_shots, hits, takeaways, giveaways,
                    pp_chances, pp_goals, pk_goals_against, NULL, sh_goals, pim_per_game,
                    attendance_home, attendance_away, sellouts_home, sellouts_away, capacity_use_pct
                FROM team_season_aggregates_old
                """
            )
        )
        conn.execute(text("DROP TABLE team_season_aggregates_old"))
        mx = conn.execute(text("SELECT MAX(id) FROM team_season_aggregates")).scalar()
        if mx is not None:
            conn.execute(text("DELETE FROM sqlite_sequence WHERE name='team_season_aggregates'"))
            conn.execute(
                text("INSERT INTO sqlite_sequence (name, seq) VALUES ('team_season_aggregates', :mx)"),
                {"mx": mx},
            )
        conn.commit()


def ensure_team_season_aggregate_extra_columns(engine: Engine) -> None:
    """Add/rename columns introduced after initial migrations (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_season_aggregates'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(team_season_aggregates)"))}
        if "sh_chances" in cols:
            return
        if "pk_chances" in cols:
            conn.execute(
                text("ALTER TABLE team_season_aggregates RENAME COLUMN pk_chances TO sh_chances")
            )
        else:
            conn.execute(text("ALTER TABLE team_season_aggregates ADD COLUMN sh_chances INTEGER"))
        conn.commit()


def ensure_homepage_performance_indexes_sqlite(engine: Engine) -> None:
    """Add SQLite indexes for query-heavy homepage dashboard builders."""
    if engine.dialect.name != "sqlite":
        return
    index_sql = (
        "CREATE INDEX IF NOT EXISTS ix_games_homepage_status_date "
        "ON games(season_id, status, game_date, id)",
        "CREATE INDEX IF NOT EXISTS ix_games_homepage_date "
        "ON games(season_id, game_date, id)",
        "CREATE INDEX IF NOT EXISTS ix_games_homepage_unplayed "
        "ON games(season_id, status, home_team_id, away_team_id)",
        "CREATE INDEX IF NOT EXISTS ix_game_skater_stats_game "
        "ON game_skater_stats(game_id)",
        "CREATE INDEX IF NOT EXISTS ix_game_skater_stats_player_game "
        "ON game_skater_stats(player_id, game_id)",
        "CREATE INDEX IF NOT EXISTS ix_game_goalie_stats_game "
        "ON game_goalie_stats(game_id)",
        "CREATE INDEX IF NOT EXISTS ix_team_standings_homepage "
        "ON team_standings(season_id, pts, w, team_id)",
        "CREATE INDEX IF NOT EXISTS ix_team_agg_homepage "
        "ON team_season_aggregates(season_id, stat_segment, team_id)",
        "CREATE INDEX IF NOT EXISTS ix_player_skater_homepage_gp "
        "ON player_skater_stats(season_id, stat_segment, gp, points, goals)",
        "CREATE INDEX IF NOT EXISTS ix_player_goalie_homepage_minutes "
        "ON player_goalie_stats(season_id, stat_segment, minutes_played, wins)",
        "CREATE INDEX IF NOT EXISTS ix_skater_career_homepage_rookie "
        "ON player_skater_career_lines(player_id, career_source, league_fhm_id, season_year)",
        "CREATE INDEX IF NOT EXISTS ix_goalie_career_homepage_rookie "
        "ON player_goalie_career_lines(player_id, career_source, league_fhm_id, season_year)",
        "CREATE INDEX IF NOT EXISTS ix_player_contracts_homepage_salary "
        "ON player_contracts(average_salary)",
    )
    with engine.connect() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for sql in index_sql:
            table_name = sql.split(" ON ", 1)[1].split("(", 1)[0].strip()
            if table_name in existing_tables:
                conn.execute(text(sql))
        conn.commit()


def ensure_players_jersey_number_sqlite(engine: Engine) -> None:
    """Add jersey_number to players when missing (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(players)"))}
        if "jersey_number" in cols:
            return
        conn.execute(text("ALTER TABLE players ADD COLUMN jersey_number INTEGER"))
        conn.commit()


def ensure_players_boost_tier_sqlite(engine: Engine) -> None:
    """Add admin-managed gold/silver boost markers to players when missing (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(players)"))}
        if "boost_tier" in cols:
            return
        conn.execute(text("ALTER TABLE players ADD COLUMN boost_tier VARCHAR(16) NOT NULL DEFAULT ''"))
        conn.commit()


def ensure_player_overall_baseline_sqlite(engine: Engine) -> None:
    """Create player_overall_baselines for post-update trend arrows (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_overall_baselines'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE player_overall_baselines (
                    player_id INTEGER NOT NULL PRIMARY KEY,
                    baseline_score INTEGER NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players (id)
                )
                """
            )
        )
        conn.commit()


def sqlite_wal_checkpoint(path: Path) -> None:
    """Merge WAL sidecar files into the main database (best-effort)."""
    db_path = Path(path).resolve()
    if not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
    except sqlite3.Error:
        return
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def sqlite_integrity_message(path: Path) -> str:
    """Return ``ok`` or the first integrity-check failure line."""
    db_path = Path(path).resolve()
    if not db_path.is_file():
        return "missing"
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        msg = str(row[0] if row else "").strip()
        return msg or "unknown"
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        conn.close()


def sqlite_is_healthy(path: Path) -> bool:
    return sqlite_integrity_message(path).lower() == "ok"


def reset_player_rating_snapshots_sqlite(engine: Engine) -> None:
    """Drop and recreate player_rating_snapshots (derived trend data; safe to rebuild)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS player_rating_snapshots"))
        conn.commit()
    ensure_player_rating_snapshots_sqlite(engine)


_CAREER_LINE_TABLES = frozenset({
    "player_skater_career_lines",
    "player_goalie_career_lines",
})

_CAREER_LINE_REQUIRED_COLS = (
    "player_id",
    "season_year",
    "team_fhm_id",
    "league_fhm_id",
    "career_source",
)


def _relax_career_line_create_sql(create_sql: str) -> str:
    """Drop all NOT NULL on career-line tables so corrupt recovered rows can load."""
    if not any(table in create_sql for table in _CAREER_LINE_TABLES):
        return create_sql
    return re.sub(r"\s+NOT NULL\b", "", create_sql, flags=re.IGNORECASE)


def _relax_recovery_sql_for_load(sql: str) -> str:
    """Relax career-line CREATE statements in sqlite3 ``.recover`` output."""

    def _repl(match: re.Match[str]) -> str:
        return _relax_career_line_create_sql(match.group(0))

    return re.sub(
        r"CREATE TABLE(?: IF NOT EXISTS)?[^;]+;",
        _repl,
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _purge_invalid_recovered_career_lines(conn: sqlite3.Connection) -> None:
    """Remove career lines sqlite3 ``.recover`` could not reconstruct fully."""
    null_checks = " OR ".join(f"{col} IS NULL" for col in _CAREER_LINE_REQUIRED_COLS)
    for table in sorted(_CAREER_LINE_TABLES):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        conn.execute(
            f"DELETE FROM {table} WHERE {null_checks} OR TRIM(COALESCE(career_source, '')) = ''"
        )
    conn.commit()


def _dedupe_recovered_career_lines(conn: sqlite3.Connection) -> None:
    """Keep one row per career-line identity when recovery emitted overlapping fragments."""
    key_cols = ", ".join(_CAREER_LINE_REQUIRED_COLS)
    for table in sorted(_CAREER_LINE_TABLES):
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM {table}
                GROUP BY {key_cols}
            )
            """
        )
    conn.commit()


def _split_sql_script(sql: str) -> list[str]:
    """Split a sqlite3 ``.recover`` script into individual statements."""
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines(keepends=True):
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    remnant = "".join(buf).strip()
    if remnant:
        statements.append(remnant)
    return statements


def _recovery_insert_table(stmt: str) -> str | None:
    upper = stmt.lstrip().upper()
    if not upper.startswith("INSERT"):
        return None
    body = stmt.split("INTO", 1)[-1].strip()
    return body.split(None, 1)[0].strip().strip('"').strip("`").strip("[")


def _sqlite_execute_recovery_statement(conn: sqlite3.Connection, stmt: str) -> None:
    """Execute one recovered statement, skipping INSERT rows the corrupt file could not rebuild."""
    if _recovery_insert_table(stmt) is None:
        conn.execute(stmt)
        return
    try:
        conn.execute(stmt)
    except (sqlite3.IntegrityError, sqlite3.DatabaseError):
        return


def _sqlite_load_recovery_sql(dst: Path, sql: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        dst.unlink()
    with sqlite3.connect(str(dst), timeout=30.0) as conn:
        for stmt in _split_sql_script(sql):
            if stmt.startswith("CREATE"):
                stmt = _relax_career_line_create_sql(stmt)
            _sqlite_execute_recovery_statement(conn, stmt)
        conn.commit()
        _purge_invalid_recovered_career_lines(conn)
        _dedupe_recovered_career_lines(conn)


def _sqlite_backup(path: Path) -> Path:
    db_path = Path(path).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db_path.with_name(f"{db_path.name}.corrupt-{stamp}.bak")
    shutil.copy2(db_path, backup)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(str(backup) + suffix))
    return backup


def _sqlite_recover_via_cli(src: Path, dst: Path) -> None:
    recover = subprocess.run(
        ["sqlite3", str(src), ".recover"],
        capture_output=True,
        text=True,
        check=False,
    )
    if recover.returncode != 0:
        raise RuntimeError(
            f"sqlite3 .recover failed ({recover.returncode}): "
            f"{(recover.stderr or recover.stdout or '').strip()}"
        )
    sql = _relax_recovery_sql_for_load(recover.stdout.strip())
    if not sql:
        raise RuntimeError("sqlite3 .recover produced no SQL")
    _sqlite_load_recovery_sql(dst, sql)


def _sqlite_recover_via_dump(src: Path, dst: Path) -> None:
    src_conn = sqlite3.connect(str(src), timeout=30.0)
    dst_conn = sqlite3.connect(str(dst), timeout=30.0)
    try:
        for line in src_conn.iterdump():
            stmt = _relax_career_line_create_sql(line) if line.startswith("CREATE") else line
            _sqlite_execute_recovery_statement(dst_conn, stmt)
        dst_conn.commit()
        _purge_invalid_recovered_career_lines(dst_conn)
        _dedupe_recovered_career_lines(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()


def recover_sqlite_database(path: Path) -> Path:
    """Backup a corrupt DB, rebuild into a temp file, verify, and replace the original."""
    db_path = Path(path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    sqlite_wal_checkpoint(db_path)
    backup = _sqlite_backup(db_path)
    rebuilt = db_path.with_suffix(db_path.suffix + ".rebuilt")
    if rebuilt.is_file():
        rebuilt.unlink()
    errors: list[str] = []
    try:
        _sqlite_recover_via_dump(db_path, rebuilt)
    except sqlite3.DatabaseError as exc:
        errors.append(f"iterdump: {exc}")
        if rebuilt.is_file():
            rebuilt.unlink()
        try:
            _sqlite_recover_via_cli(db_path, rebuilt)
        except (RuntimeError, OSError) as exc2:
            errors.append(f"recover: {exc2}")
            raise RuntimeError(
                f"Could not rebuild {db_path.name}; backup at {backup.name}. "
                + "; ".join(errors)
            ) from exc2
    if not sqlite_is_healthy(rebuilt):
        raise RuntimeError(
            f"Rebuilt {rebuilt.name} still fails integrity_check; backup at {backup.name}"
        )
    db_path.unlink(missing_ok=True)
    rebuilt.replace(db_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.is_file():
            sidecar.unlink()
    return backup


def prepare_sqlite_database(path: Path, *, auto_repair: bool = False) -> tuple[bool, str]:
    """Checkpoint WAL, verify integrity, and optionally rebuild a corrupt database."""
    db_path = Path(path).resolve()
    sqlite_wal_checkpoint(db_path)
    msg = sqlite_integrity_message(db_path)
    if msg.lower() == "ok":
        return True, msg
    if not auto_repair or not db_path.is_file():
        return False, msg
    try:
        backup = recover_sqlite_database(db_path)
        msg = sqlite_integrity_message(db_path)
        if msg.lower() == "ok":
            return True, f"repaired (backup {backup.name})"
        return False, msg
    except Exception as exc:
        return False, str(exc)


def ensure_player_rating_snapshots_sqlite(engine: Engine) -> None:
    """Create player_rating_snapshots for development panel trend lines (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_rating_snapshots'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE player_rating_snapshots (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    league_slug VARCHAR(64) NOT NULL,
                    snapshot_at DATETIME NOT NULL,
                    ratings_json TEXT NOT NULL,
                    ability FLOAT,
                    potential FLOAT,
                    overall_score INTEGER,
                    FOREIGN KEY(player_id) REFERENCES players (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_player_rating_snapshots_player_at "
                "ON player_rating_snapshots (player_id, snapshot_at)"
            )
        )
        conn.commit()


def ensure_skater_career_line_career_source_sqlite(engine: Engine) -> None:
    """Add career_source to player_skater_career_lines when missing (pre-unique-key schema)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_skater_career_lines'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(player_skater_career_lines)"))}
        if "career_source" in cols:
            return
        conn.execute(
            text(
                "ALTER TABLE player_skater_career_lines "
                "ADD COLUMN career_source VARCHAR(24) NOT NULL DEFAULT 'rs'"
            )
        )
        conn.commit()


def ensure_skater_career_line_extra_stats_sqlite(engine: Engine) -> None:
    """Add gwg, gva, tka, sb to player_skater_career_lines when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_skater_career_lines'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(player_skater_career_lines)"))}
        alters: list[str] = []
        if "gwg" not in cols:
            alters.append("ALTER TABLE player_skater_career_lines ADD COLUMN gwg INTEGER")
        if "gva" not in cols:
            alters.append("ALTER TABLE player_skater_career_lines ADD COLUMN gva INTEGER")
        if "tka" not in cols:
            alters.append("ALTER TABLE player_skater_career_lines ADD COLUMN tka INTEGER")
        if "sb" not in cols:
            alters.append("ALTER TABLE player_skater_career_lines ADD COLUMN sb INTEGER")
        for stmt in alters:
            conn.execute(text(stmt))
        if alters:
            conn.commit()


def ensure_skater_career_line_game_rating_sqlite(engine: Engine) -> None:
    """Add game_rating (FHM season GR) to player_skater_career_lines when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_skater_career_lines'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(player_skater_career_lines)"))}
        if "game_rating" in cols:
            return
        conn.execute(text("ALTER TABLE player_skater_career_lines ADD COLUMN game_rating FLOAT"))
        conn.commit()


def ensure_history_awards_staff_fhm_id_sqlite(engine: Engine) -> None:
    """Add ``staff_fhm_id`` to ``history_awards`` when missing (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_awards'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(history_awards)"))}
        if "staff_fhm_id" in cols:
            return
        conn.execute(text("ALTER TABLE history_awards ADD COLUMN staff_fhm_id VARCHAR(64)"))
        conn.commit()


def ensure_history_records_admin_metadata_sqlite(engine: Engine) -> None:
    """Add ``source`` / ``updated_at`` / ``updated_by_user_id`` on admin-managed history tables."""
    if engine.dialect.name != "sqlite":
        return
    specs = (
        "history_awards",
        "history_all_stars",
        "team_season_records",
        "hall_of_fame_members",
    )
    with engine.connect() as conn:
        for table in specs:
            exists = conn.execute(
                text(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table}'")
            ).fetchone()
            if not exists:
                continue
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if "source" not in cols:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'csv'"
                    )
                )
            if "updated_at" not in cols:
                # SQLite only allows constant defaults on ALTER TABLE ADD COLUMN.
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME"))
                conn.execute(
                    text(f"UPDATE {table} SET updated_at = datetime('now') WHERE updated_at IS NULL")
                )
            if "updated_by_user_id" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN updated_by_user_id INTEGER"))
        conn.commit()


def ensure_history_all_stars_sqlite(engine: Engine) -> None:
    """Drop and recreate ``history_all_stars`` when an older table lacked ``season_label``."""
    if engine.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect

    from app.models import HistoryAllStar

    insp = inspect(engine)
    if not insp.has_table("history_all_stars"):
        return
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(history_all_stars)"))}
    if "season_label" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE history_all_stars"))
    HistoryAllStar.__table__.create(bind=engine, checkfirst=True)


def ensure_player_goalie_stats_gsaa_sqlite(engine: Engine) -> None:
    """Add GSAA when missing (SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_goalie_stats'"
            )
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(player_goalie_stats)"))}
        if "gsaa" in cols:
            return
        conn.execute(text("ALTER TABLE player_goalie_stats ADD COLUMN gsaa REAL"))
        conn.commit()


def _ensure_sqlite_columns(engine: Engine, table: str, columns: dict[str, str]) -> None:
    """Add missing columns on SQLite league DBs."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).fetchone()
        if not exists:
            return
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        for name, col_type in columns.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))
        conn.commit()


def ensure_advanced_stats_columns_sqlite(engine: Engine) -> None:
    """Add advanced process-stats columns when missing (SQLite)."""
    _ensure_sqlite_columns(
        engine,
        "player_skater_stats",
        {
            "cf": "INTEGER",
            "ca": "INTEGER",
            "cf_pct": "REAL",
            "cf_pct_rel": "REAL",
            "ff": "INTEGER",
            "fa": "INTEGER",
            "ff_pct": "REAL",
            "ff_pct_rel": "REAL",
            "gf_per_60": "REAL",
            "ga_per_60": "REAL",
            "sf_per_60": "REAL",
            "sa_per_60": "REAL",
        },
    )
    _ensure_sqlite_columns(
        engine,
        "game_skater_stats",
        {
            "oz_starts": "INTEGER",
            "nz_starts": "INTEGER",
            "dz_starts": "INTEGER",
            "sq0": "INTEGER",
            "sq1": "INTEGER",
            "sq2": "INTEGER",
            "sq3": "INTEGER",
            "sq4": "INTEGER",
            "team_shots_off": "INTEGER",
            "team_shots_against_off": "INTEGER",
            "team_goals_off": "INTEGER",
            "team_goal_against_off": "INTEGER",
        },
    )
    _ensure_sqlite_columns(
        engine,
        "games",
        {
            "sq0_home": "INTEGER",
            "sq1_home": "INTEGER",
            "sq2_home": "INTEGER",
            "sq3_home": "INTEGER",
            "sq4_home": "INTEGER",
            "sq0_away": "INTEGER",
            "sq1_away": "INTEGER",
            "sq2_away": "INTEGER",
            "sq3_away": "INTEGER",
            "sq4_away": "INTEGER",
            "sog_home_p1": "INTEGER",
            "sog_home_p2": "INTEGER",
            "sog_home_p3": "INTEGER",
            "sog_home_ot": "INTEGER",
            "sog_away_p1": "INTEGER",
            "sog_away_p2": "INTEGER",
            "sog_away_p3": "INTEGER",
            "sog_away_ot": "INTEGER",
            "score_home_p1": "INTEGER",
            "score_home_p2": "INTEGER",
            "score_home_p3": "INTEGER",
            "score_home_ot": "INTEGER",
            "score_away_p1": "INTEGER",
            "score_away_p2": "INTEGER",
            "score_away_p3": "INTEGER",
            "score_away_ot": "INTEGER",
        },
    )


_FTS5_SHADOW_SUFFIXES = ("_data", "_idx", "_docsize", "_config", "_content")


def _fts5_shadow_table_names(table_name: str) -> tuple[str, ...]:
    return tuple(f"{table_name}{suffix}" for suffix in _FTS5_SHADOW_SUFFIXES)


def _drop_sqlite_table_if_exists(conn, table_name: str) -> None:
    try:
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    except DatabaseError:
        pass


def _purge_sqlite_fts5_from_schema(conn, table_name: str) -> None:
    """Remove FTS5 vtable and shadow entries from sqlite_master."""
    conn.execute(text("PRAGMA writable_schema=ON"))
    try:
        conn.execute(
            text("DELETE FROM sqlite_master WHERE name = :tbl OR tbl_name = :tbl"),
            {"tbl": table_name},
        )
        for shadow in _fts5_shadow_table_names(table_name):
            conn.execute(
                text("DELETE FROM sqlite_master WHERE name = :shadow"),
                {"shadow": shadow},
            )
        conn.commit()
    finally:
        conn.execute(text("PRAGMA writable_schema=OFF"))
        conn.commit()


def _drop_sqlite_fts5_shadow_tables(conn, table_name: str) -> None:
    for shadow in _fts5_shadow_table_names(table_name):
        _drop_sqlite_table_if_exists(conn, shadow)
    conn.commit()


def _cleanup_sqlite_fts5_orphans(conn, table_name: str) -> None:
    """Drop leftover FTS5 shadow tables when the virtual table entry is missing."""
    exists = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :tbl"),
        {"tbl": table_name},
    ).fetchone()
    if exists:
        return
    _purge_sqlite_fts5_from_schema(conn, table_name)
    _drop_sqlite_fts5_shadow_tables(conn, table_name)


def _drop_sqlite_fts5_table(conn, table_name: str) -> None:
    """Drop an FTS5 virtual table, scrubbing sqlite_master if the vtable is corrupt."""
    try:
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    except DatabaseError as exc:
        msg = str(exc).lower()
        if "vtable constructor failed" not in msg and "malformed" not in msg:
            raise
    _purge_sqlite_fts5_from_schema(conn, table_name)
    _drop_sqlite_fts5_shadow_tables(conn, table_name)


_PLAYER_FTS_DDL = """
CREATE VIRTUAL TABLE player_fts USING fts5(
    full_name,
    position,
    team_abbrev,
    player_id UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""

_PLAYER_FTS_INSERT = """
INSERT INTO player_fts (rowid, full_name, position, team_abbrev, player_id)
SELECT
    p.id,
    p.full_name,
    COALESCE(p.position, ''),
    COALESCE(t.abbreviation, ''),
    p.id
FROM players p
LEFT JOIN teams t ON t.id = p.current_team_id;
"""


def _sqlite_path_from_engine(engine: Engine) -> Path | None:
    if engine.dialect.name != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def _is_sqlite_corruption_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "vtable constructor failed",
            "malformed",
            "database disk image is malformed",
            "out of order",
        )
    )


def _create_player_fts5_table(conn) -> None:
    try:
        conn.execute(text(_PLAYER_FTS_DDL))
    except DatabaseError as exc:
        if "already exists" not in str(exc).lower():
            raise
        _purge_sqlite_fts5_from_schema(conn, "player_fts")
        _drop_sqlite_fts5_shadow_tables(conn, "player_fts")
        conn.execute(text(_PLAYER_FTS_DDL))


def _populate_player_fts(conn) -> None:
    conn.execute(text(_PLAYER_FTS_INSERT))


def _player_fts_exists(conn) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE name LIKE 'player_fts%' LIMIT 1")
        ).fetchone()
        is not None
    )


def _force_remove_player_fts(conn) -> None:
    """Drop player_fts and shadow tables, retrying schema scrub when the file is corrupt."""
    for _ in range(3):
        _drop_sqlite_fts5_table(conn, "player_fts")
        if not _player_fts_exists(conn):
            return
    raise DatabaseError("Could not remove corrupt player_fts table", None, None)


def _rebuild_player_fts_once(engine: Engine) -> None:
    with engine.connect() as conn:
        _force_remove_player_fts(conn)
        _create_player_fts5_table(conn)
        _populate_player_fts(conn)
        conn.commit()


def ensure_fts5(engine: Engine) -> None:
    """Create the player search virtual table if missing."""
    with engine.connect() as conn:
        _cleanup_sqlite_fts5_orphans(conn, "player_fts")
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_fts'")
        ).fetchone()
        if not exists:
            _create_player_fts5_table(conn)
        conn.commit()


def rebuild_player_fts(engine: Engine, *, auto_repair_db: bool = False) -> None:
    """Rebuild FTS index from players + current team.

    Drop and recreate the virtual table instead of ``DELETE`` so a corrupted FTS
    segment does not fail the whole import (derived index; safe to replace).

    When ``auto_repair_db`` is true and SQLite reports btree/vtable corruption,
    rebuild the database file via dump/recover and retry once.
    """
    try:
        _rebuild_player_fts_once(engine)
    except DatabaseError as exc:
        msg = str(exc).lower()
        corruption = _is_sqlite_corruption_error(exc) or (
            "already exists" in msg and "player_fts" in msg
        )
        if not auto_repair_db or not corruption:
            raise
        db_path = _sqlite_path_from_engine(engine)
        if db_path is None or not db_path.is_file():
            raise
        engine.dispose()
        recover_sqlite_database(db_path)
        _rebuild_player_fts_once(engine)


def repair_fhm_team_city_from_name(engine: Engine) -> None:
    """Set ``teams.city`` to match ``teams.name`` for FHM imports (city was wrongly ``name.split()[0]``)."""
    if engine.dialect.name != "sqlite":
        return
    stmt = text(
        """
        UPDATE teams
        SET city = name
        WHERE fhm_team_id IS NOT NULL
          AND name IS NOT NULL
          AND TRIM(name) != ''
          AND (city IS NULL OR TRIM(city) = '' OR city != name)
        """
    )
    attempts = 5
    delay_s = 0.15
    for attempt in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(stmt)
                conn.commit()
            return
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt >= attempts - 1:
                raise
            time.sleep(delay_s * (attempt + 1))


def ensure_homepage_module_settings_sqlite(engine: Engine) -> None:
    """Create homepage module settings table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='homepage_module_settings'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE homepage_module_settings (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    module_key VARCHAR(64) NOT NULL,
                    is_enabled BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    updated_by_user_id INTEGER,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_home_mod_league_key "
                "ON homepage_module_settings (league_slug, module_key)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_home_mod_league_sort "
                "ON homepage_module_settings (league_slug, sort_order)"
            )
        )
        conn.commit()


def ensure_site_announcements_sqlite(engine: Engine) -> None:
    """Create site announcements table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_announcements'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE site_announcements (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    title VARCHAR(200) NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    level VARCHAR(16) NOT NULL DEFAULT 'info',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    starts_at DATETIME,
                    ends_at DATETIME,
                    created_by_user_id INTEGER,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_site_announce_league_active "
                "ON site_announcements (league_slug, is_active)"
            )
        )
        conn.commit()


def ensure_site_users_admin_role_sqlite(engine: Engine) -> None:
    """Add missing site_users profile/admin columns (site DB, SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_users'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(site_users)"))}
        if "discord_user_id" not in cols:
            conn.execute(text("ALTER TABLE site_users ADD COLUMN discord_user_id VARCHAR(32)"))
        if "discord_dm_enabled" not in cols:
            conn.execute(
                text("ALTER TABLE site_users ADD COLUMN discord_dm_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "admin_role" not in cols:
            conn.execute(text("ALTER TABLE site_users ADD COLUMN admin_role VARCHAR(32)"))
        idx = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_site_users_admin_role'"
            )
        ).fetchone()
        if not idx:
            conn.execute(text("CREATE INDEX ix_site_users_admin_role ON site_users (admin_role)"))
        conn.commit()


def ensure_password_reset_tokens_sqlite(engine: Engine) -> None:
    """Create password reset token table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE password_reset_tokens (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    created_at DATETIME NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES site_users (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_password_reset_tokens_token_hash "
                "ON password_reset_tokens (token_hash)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_pwd_reset_lookup "
                "ON password_reset_tokens (token_hash, used_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_pwd_reset_user_created "
                "ON password_reset_tokens (user_id, created_at)"
            )
        )
        conn.commit()


def ensure_site_banned_identities_sqlite(engine: Engine) -> None:
    """Create site ban list table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_banned_identities'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE site_banned_identities (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    email_norm VARCHAR(255) NOT NULL,
                    discord_name VARCHAR(120) NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    league_slug VARCHAR(64) NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    created_by_user_id INTEGER,
                    FOREIGN KEY(created_by_user_id) REFERENCES site_users (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_site_banned_email_norm "
                "ON site_banned_identities (email_norm)"
            )
        )
        conn.commit()


def ensure_league_rule_settings_sqlite(engine: Engine) -> None:
    """Create league rule settings table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='league_rule_settings'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE league_rule_settings (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    rule_key VARCHAR(80) NOT NULL,
                    rule_value TEXT NOT NULL DEFAULT '',
                    updated_by_user_id INTEGER,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_league_rule_key "
                "ON league_rule_settings (league_slug, rule_key)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_league_rule_league "
                "ON league_rule_settings (league_slug)"
            )
        )
        conn.commit()


def ensure_gm_approval_requests_sqlite(engine: Engine) -> None:
    """Create GM approval requests table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gm_approval_requests'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE gm_approval_requests (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    team_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    request_type VARCHAR(32) NOT NULL,
                    title VARCHAR(200) NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    admin_note TEXT NOT NULL DEFAULT '',
                    processed_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    processed_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_approval_league_status "
                "ON gm_approval_requests (league_slug, status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_approval_team "
                "ON gm_approval_requests (league_slug, team_id)"
            )
        )
        conn.commit()


def ensure_staff_change_requests_sqlite(engine: Engine) -> None:
    """Create staff hire/fire request table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staff_change_requests'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE staff_change_requests (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    season_start_year INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    request_type VARCHAR(16) NOT NULL,
                    role VARCHAR(32),
                    staff_fhm_id VARCHAR(64) NOT NULL,
                    staff_name VARCHAR(200) NOT NULL DEFAULT '',
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    admin_note TEXT NOT NULL DEFAULT '',
                    processed_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    processed_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES site_users (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_staff_change_league_status "
                "ON staff_change_requests (league_slug, status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_staff_change_team "
                "ON staff_change_requests (league_slug, team_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_staff_change_staff "
                "ON staff_change_requests (league_slug, staff_fhm_id)"
            )
        )
        conn.commit()


def ensure_team_staff_roster_entries_sqlite(engine: Engine) -> None:
    """Create approved staff roster table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_staff_roster_entries'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE team_staff_roster_entries (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    season_start_year INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    staff_fhm_id VARCHAR(64) NOT NULL,
                    staff_name VARCHAR(200) NOT NULL DEFAULT '',
                    role VARCHAR(32) NOT NULL,
                    hire_request_id INTEGER,
                    hired_at DATETIME NOT NULL,
                    fired_at DATETIME,
                    FOREIGN KEY(hire_request_id) REFERENCES staff_change_requests (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_team_staff_roster_league_team "
                "ON team_staff_roster_entries (league_slug, team_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_team_staff_roster_staff "
                "ON team_staff_roster_entries (league_slug, staff_fhm_id)"
            )
        )
        conn.commit()


def ensure_team_cap_penalties_sqlite(engine: Engine) -> None:
    """Create ``team_cap_penalties`` on the site DB when missing."""
    from app.site_models import TeamCapPenalty

    TeamCapPenalty.__table__.create(bind=engine, checkfirst=True)


def ensure_team_staff_budget_current_salary_sqlite(engine: Engine) -> None:
    """Add ``current_salary_amount`` to ``team_staff_budgets`` when upgrading (SQLite or MySQL)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    if not insp.has_table("team_staff_budgets"):
        return
    colnames = {col["name"] for col in insp.get_columns("team_staff_budgets")}
    if "current_salary_amount" in colnames:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE team_staff_budgets "
                "ADD COLUMN current_salary_amount INTEGER NOT NULL DEFAULT 0"
            )
        )


def ensure_discord_playoff_bracket_sqlite(engine: Engine) -> None:
    """Playoff bracket Discord series posts + bot-config fingerprint column (SQLite or MySQL)."""
    from app.site_models import DiscordPlayoffBracketSeriesPost

    DiscordPlayoffBracketSeriesPost.__table__.create(bind=engine, checkfirst=True)
    from sqlalchemy import inspect

    insp = inspect(engine)
    if not insp.has_table("discord_league_bot_config"):
        return
    colnames = {col["name"] for col in insp.get_columns("discord_league_bot_config")}
    if "playoff_bracket_fingerprint" in colnames:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE discord_league_bot_config "
                "ADD COLUMN playoff_bracket_fingerprint VARCHAR(128) NOT NULL DEFAULT ''"
            )
        )


def ensure_gm_trade_proposals_sqlite(engine: Engine) -> None:
    """Create GM trade proposals table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gm_trade_proposals'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE gm_trade_proposals (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    from_user_id INTEGER NOT NULL,
                    from_team_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    to_team_id INTEGER NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending_partner',
                    ledger_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    commissioner_note TEXT NOT NULL DEFAULT '',
                    commissioner_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    partner_acted_at DATETIME,
                    commissioner_acted_at DATETIME,
                    FOREIGN KEY(from_user_id) REFERENCES site_users (id),
                    FOREIGN KEY(to_user_id) REFERENCES site_users (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_trade_league_status "
                "ON gm_trade_proposals (league_slug, status)"
            )
        )
        conn.commit()


def ensure_trade_market_sqlite(engine: Engine) -> None:
    """Create Trade Market tables on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        if not conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='trade_market_draft_pick_ownership'"
            )
        ).fetchone():
            conn.execute(
                text(
                    """
                    CREATE TABLE trade_market_draft_pick_ownership (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        draft_year INTEGER NOT NULL,
                        original_team_fhm_id INTEGER NOT NULL,
                        original_team_id INTEGER,
                        round INTEGER NOT NULL,
                        owner_team_fhm_id INTEGER NOT NULL,
                        owner_team_id INTEGER,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_trade_dpick_league_year_orig_round "
                    "ON trade_market_draft_pick_ownership "
                    "(league_slug, draft_year, original_team_fhm_id, round)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_trade_dpick_owner "
                    "ON trade_market_draft_pick_ownership (league_slug, owner_team_id)"
                )
            )
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_market_listings'")
        ).fetchone():
            conn.execute(
                text(
                    """
                    CREATE TABLE trade_market_listings (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        user_id INTEGER NOT NULL,
                        team_id INTEGER NOT NULL,
                        asset_type VARCHAR(24) NOT NULL,
                        asset_ref VARCHAR(120) NOT NULL,
                        asking_price VARCHAR(120) NOT NULL DEFAULT '',
                        wants_json TEXT NOT NULL DEFAULT '[]',
                        note TEXT NOT NULL DEFAULT '',
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        discord_payload_hash VARCHAR(64),
                        posted_game_date DATE,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES site_users (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_trade_market_listing_league_team "
                    "ON trade_market_listings (league_slug, team_id, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_trade_market_listing_league_asset "
                    "ON trade_market_listings (league_slug, asset_type, asset_ref)"
                )
            )
        else:
            cols = {
                str(row[1])
                for row in conn.execute(text("PRAGMA table_info(trade_market_listings)")).fetchall()
            }
            if "posted_game_date" not in cols:
                conn.execute(text("ALTER TABLE trade_market_listings ADD COLUMN posted_game_date DATE"))
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_market_buying_needs'")
        ).fetchone():
            conn.execute(
                text(
                    """
                    CREATE TABLE trade_market_buying_needs (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        user_id INTEGER NOT NULL,
                        team_id INTEGER NOT NULL,
                        category VARCHAR(40) NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        discord_payload_hash VARCHAR(64),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES site_users (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_trade_market_buying_team_category "
                    "ON trade_market_buying_needs (league_slug, team_id, category)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_trade_market_buying_league "
                    "ON trade_market_buying_needs (league_slug, status)"
                )
            )
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='draft_pick_ownership_years'")
        ).fetchone():
            conn.execute(
                text(
                    """
                    CREATE TABLE draft_pick_ownership_years (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        draft_year INTEGER NOT NULL,
                        round_count INTEGER NOT NULL DEFAULT 10,
                        status VARCHAR(24) NOT NULL DEFAULT 'active',
                        display_order INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_dpick_year_league_year "
                    "ON draft_pick_ownership_years (league_slug, draft_year)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_dpick_year_league_status_order "
                    "ON draft_pick_ownership_years (league_slug, status, display_order)"
                )
            )
        conn.commit()


def ensure_story_publish_schedules_sqlite(engine: Engine) -> None:
    """Create story publish schedules table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_publish_schedules'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE story_publish_schedules (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    article_id INTEGER NOT NULL,
                    channel VARCHAR(24) NOT NULL DEFAULT 'site',
                    status VARCHAR(24) NOT NULL DEFAULT 'scheduled',
                    scheduled_for_utc DATETIME NOT NULL,
                    dry_run_only BOOLEAN NOT NULL DEFAULT 1,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    last_result_json TEXT NOT NULL DEFAULT '{}',
                    created_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    processed_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_story_sched_league_status "
                "ON story_publish_schedules (league_slug, status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_story_sched_run_at "
                "ON story_publish_schedules (scheduled_for_utc)"
            )
        )
        conn.commit()


def ensure_story_publish_schedule_extra_columns_sqlite(engine: Engine) -> None:
    """Add attempt_count / last_error / last_attempt_at to story_publish_schedules when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_publish_schedules'")
        ).fetchone()
        if not exists:
            return
        cols = {str(r[1]) for r in conn.execute(text("PRAGMA table_info(story_publish_schedules)")).fetchall()}
        if "attempt_count" not in cols:
            conn.execute(text("ALTER TABLE story_publish_schedules ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"))
        if "last_error" not in cols:
            conn.execute(text("ALTER TABLE story_publish_schedules ADD COLUMN last_error TEXT NOT NULL DEFAULT ''"))
        if "last_attempt_at" not in cols:
            conn.execute(text("ALTER TABLE story_publish_schedules ADD COLUMN last_attempt_at DATETIME"))
        conn.commit()


def ensure_awards_voting_sqlite(engine: Engine) -> None:
    """Create awards voting scaffold tables on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        has_cycles = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='awards_voting_cycles'")
        ).fetchone()
        if not has_cycles:
            conn.execute(
                text(
                    """
                    CREATE TABLE awards_voting_cycles (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        season_label VARCHAR(80) NOT NULL DEFAULT '',
                        title VARCHAR(160) NOT NULL DEFAULT '',
                        status VARCHAR(24) NOT NULL DEFAULT 'open',
                        opens_at DATETIME,
                        closes_at DATETIME,
                        created_by_user_id INTEGER,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_awards_cycle_league_status "
                    "ON awards_voting_cycles (league_slug, status)"
                )
            )
        has_ballots = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='awards_vote_ballots'")
        ).fetchone()
        if not has_ballots:
            conn.execute(
                text(
                    """
                    CREATE TABLE awards_vote_ballots (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        cycle_id INTEGER NOT NULL,
                        award_key VARCHAR(64) NOT NULL,
                        voter_user_id INTEGER NOT NULL,
                        candidate_ref VARCHAR(120) NOT NULL,
                        rank_value INTEGER NOT NULL DEFAULT 1,
                        points_value INTEGER NOT NULL DEFAULT 0,
                        submitted_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_awards_ballot_cycle_award "
                    "ON awards_vote_ballots (league_slug, cycle_id, award_key)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_awards_ballot_voter "
                    "ON awards_vote_ballots (league_slug, voter_user_id)"
                )
            )
        conn.commit()


def ensure_news_engagement_sqlite(engine: Engine) -> None:
    """Create Around the League comment / vote tables on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        for table, ddl in (
            (
                "news_article_comments",
                """
                CREATE TABLE news_article_comments (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(article_id) REFERENCES news_articles (id),
                    FOREIGN KEY(user_id) REFERENCES site_users (id)
                )
                """,
            ),
            (
                "news_article_votes",
                """
                CREATE TABLE news_article_votes (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    value INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(article_id) REFERENCES news_articles (id),
                    FOREIGN KEY(user_id) REFERENCES site_users (id)
                )
                """,
            ),
        ):
            exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).fetchone()
            if exists:
                continue
            conn.execute(text(ddl))
            if table == "news_article_comments":
                conn.execute(
                    text(
                        "CREATE INDEX ix_news_article_comment_article "
                        "ON news_article_comments (article_id)"
                    )
                )
            else:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_news_article_vote_article_user "
                        "ON news_article_votes (article_id, user_id)"
                    )
                )
                conn.execute(
                    text("CREATE INDEX ix_news_article_vote_article ON news_article_votes (article_id)")
                )
        conn.commit()


def ensure_member_watchlists_sqlite(engine: Engine) -> None:
    """Create member watchlist scaffold table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='member_watchlist_items'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE member_watchlist_items (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    league_slug VARCHAR(64) NOT NULL,
                    target_type VARCHAR(24) NOT NULL,
                    target_ref VARCHAR(120) NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_watchlist_user_league "
                "ON member_watchlist_items (user_id, league_slug)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_watchlist_league_target "
                "ON member_watchlist_items (league_slug, target_type, target_ref)"
            )
        )
        conn.commit()


def ensure_rfa_offer_requests_sqlite(engine: Engine) -> None:
    """Create RFA offer sheet request table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rfa_offer_requests'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE rfa_offer_requests (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    offering_user_id INTEGER NOT NULL,
                    offering_team_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    player_fhm_id VARCHAR(64),
                    rights_team_id INTEGER NOT NULL,
                    rfa_category VARCHAR(16) NOT NULL,
                    category_explanation TEXT NOT NULL DEFAULT '',
                    previous_contract_salary INTEGER NOT NULL DEFAULT 0,
                    minimum_offer_salary INTEGER NOT NULL DEFAULT 0,
                    offer_salary INTEGER NOT NULL,
                    offer_years INTEGER NOT NULL,
                    special_clauses TEXT NOT NULL DEFAULT '',
                    compensation_tier_key VARCHAR(32) NOT NULL DEFAULT 'none',
                    compensation_label VARCHAR(200) NOT NULL DEFAULT '',
                    compensation_picks_json TEXT NOT NULL DEFAULT '[]',
                    compensation_draft_year INTEGER,
                    compensation_valid BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending_admin',
                    happiness VARCHAR(32),
                    player_decision_roll REAL,
                    player_accepted BOOLEAN,
                    group_iii_allows_match BOOLEAN,
                    original_team_decision VARCHAR(16),
                    original_team_user_id INTEGER,
                    original_team_decided_at DATETIME,
                    admin_note TEXT NOT NULL DEFAULT '',
                    processed_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    processed_at DATETIME,
                    FOREIGN KEY(offering_user_id) REFERENCES site_users (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_rfa_offer_league_status "
                "ON rfa_offer_requests (league_slug, status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_rfa_offer_offering_team "
                "ON rfa_offer_requests (league_slug, offering_team_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_rfa_offer_rights_team "
                "ON rfa_offer_requests (league_slug, rights_team_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_rfa_offer_player "
                "ON rfa_offer_requests (league_slug, player_id)"
            )
        )
        conn.commit()


def ensure_franchise_team_identities_sqlite(engine: Engine) -> None:
    """Create editable historical franchise identity rows in league DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='franchise_team_identities'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE franchise_team_identities (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER,
                    team_fhm_id VARCHAR(64),
                    display_name VARCHAR(200) NOT NULL,
                    abbreviation VARCHAR(16),
                    logo_file VARCHAR(500),
                    start_year INTEGER NOT NULL,
                    end_year INTEGER,
                    status VARCHAR(32) NOT NULL DEFAULT 'historical',
                    notes TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(team_id) REFERENCES teams (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_franchise_identity_team_year "
                "ON franchise_team_identities (team_id, start_year, end_year)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_franchise_identity_fhm_year "
                "ON franchise_team_identities (team_fhm_id, start_year, end_year)"
            )
        )
        conn.commit()


def ensure_team_honors_meta_sqlite(engine: Engine) -> None:
    """Create per-team honors display toggles when missing (league DB)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_honors_meta'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE team_honors_meta (
                    team_id INTEGER NOT NULL PRIMARY KEY,
                    retired_section_enabled BOOLEAN NOT NULL DEFAULT 0,
                    FOREIGN KEY(team_id) REFERENCES teams (id)
                )
                """
            )
        )
        conn.commit()


def ensure_team_retired_numbers_sqlite(engine: Engine) -> None:
    """Create franchise retired number rows when missing (league DB)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_retired_numbers'")
        ).fetchone()
        if exists:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(team_retired_numbers)"))}
            if "number_color" not in cols:
                conn.execute(text("ALTER TABLE team_retired_numbers ADD COLUMN number_color VARCHAR(16)"))
                conn.commit()
            return
        conn.execute(
            text(
                """
                CREATE TABLE team_retired_numbers (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL,
                    player_name VARCHAR(200) NOT NULL,
                    jersey_number INTEGER NOT NULL,
                    jersey_image_rel_path VARCHAR(500),
                    number_color VARCHAR(16),
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams (id),
                    CONSTRAINT uq_team_retired_jersey UNIQUE (team_id, jersey_number)
                )
                """
            )
        )
        conn.execute(
            text("CREATE INDEX ix_team_retired_numbers_team ON team_retired_numbers (team_id)")
        )
        conn.commit()


def ensure_team_victory_banners_sqlite(engine: Engine) -> None:
    """Create team victory banner rows when missing (league DB)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_victory_banners'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE team_victory_banners (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL,
                    title VARCHAR(200) NOT NULL DEFAULT '',
                    victory_number INTEGER NOT NULL,
                    banner_image_rel_path VARCHAR(500),
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams (id),
                    CONSTRAINT uq_team_victory_banner UNIQUE (team_id, victory_number)
                )
                """
            )
        )
        conn.execute(
            text("CREATE INDEX ix_team_victory_banners_team ON team_victory_banners (team_id)")
        )
        conn.commit()


def ensure_admin_undo_actions_sqlite(engine: Engine) -> None:
    """Create admin undo action table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_undo_actions'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE admin_undo_actions (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    action_key VARCHAR(64) NOT NULL,
                    entity_type VARCHAR(64) NOT NULL,
                    entity_id INTEGER NOT NULL,
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    created_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    is_reverted BOOLEAN NOT NULL DEFAULT 0,
                    reverted_by_user_id INTEGER,
                    reverted_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_admin_undo_league_created "
                "ON admin_undo_actions (league_slug, created_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_admin_undo_reverted "
                "ON admin_undo_actions (league_slug, is_reverted)"
            )
        )
        conn.commit()


def ensure_positional_rank_snapshots_sqlite(engine: Engine) -> None:
    """Create positional rank snapshot table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='positional_rank_snapshots'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE positional_rank_snapshots (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    snapshot_at DATETIME NOT NULL,
                    ranks_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_positional_rank_snap_league_at "
                "ON positional_rank_snapshots (league_slug, snapshot_at)"
            )
        )
        conn.commit()


def ensure_power_rank_snapshots_sqlite(engine: Engine) -> None:
    """Create power rank snapshot table on site DB when missing (SQLite local)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='power_rank_snapshots'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE power_rank_snapshots (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    snapshot_at DATETIME NOT NULL,
                    ranks_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        conn.execute(
            text("CREATE INDEX ix_power_rank_snap_league_at ON power_rank_snapshots (league_slug, snapshot_at)")
        )
        conn.commit()


def ensure_prospect_league_rank_snapshots_sqlite(engine: Engine) -> None:
    """Create prospect league POT rank snapshot table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prospect_league_rank_snapshots'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE prospect_league_rank_snapshots (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    snapshot_at DATETIME NOT NULL,
                    ranks_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_prospect_league_snap_league_at "
                "ON prospect_league_rank_snapshots (league_slug, snapshot_at)"
            )
        )
        conn.commit()


def ensure_discord_outbound_sqlite(engine: Engine) -> None:
    """Create Discord route + outbound event tables on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        has_routes = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_channel_routes'")
        ).fetchone()
        if not has_routes:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_channel_routes (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        event_key VARCHAR(64) NOT NULL,
                        channel_key VARCHAR(64) NOT NULL DEFAULT '',
                        is_enabled BOOLEAN NOT NULL DEFAULT 1,
                        updated_by_user_id INTEGER,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_discord_route_league_event "
                    "ON discord_channel_routes (league_slug, event_key)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_route_league_event "
                    "ON discord_channel_routes (league_slug, event_key)"
                )
            )
        has_events = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_outbound_events'")
        ).fetchone()
        if not has_events:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_outbound_events (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        event_key VARCHAR(64) NOT NULL,
                        channel_key VARCHAR(64) NOT NULL DEFAULT '',
                        idempotency_key VARCHAR(64) NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        status VARCHAR(24) NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_by_user_id INTEGER,
                        created_at DATETIME NOT NULL,
                        next_attempt_at DATETIME,
                        sent_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_event_status_created "
                    "ON discord_outbound_events (status, created_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_event_league_status "
                    "ON discord_outbound_events (league_slug, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_event_idempotency_key "
                    "ON discord_outbound_events (idempotency_key)"
                )
            )
        else:
            cols = conn.execute(text("PRAGMA table_info(discord_outbound_events)")).fetchall()
            names = {str(c[1]) for c in cols}
            if "next_attempt_at" not in names:
                conn.execute(text("ALTER TABLE discord_outbound_events ADD COLUMN next_attempt_at DATETIME"))
            if "idempotency_key" not in names:
                conn.execute(
                    text(
                        "ALTER TABLE discord_outbound_events "
                        "ADD COLUMN idempotency_key VARCHAR(64) NOT NULL DEFAULT ''"
                    )
                )
            has_idx = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_discord_event_idempotency_key'")
            ).fetchone()
            if not has_idx:
                conn.execute(
                    text(
                        "CREATE INDEX ix_discord_event_idempotency_key "
                        "ON discord_outbound_events (idempotency_key)"
                    )
                )
        has_hb = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_bot_heartbeats'")
        ).fetchone()
        if not has_hb:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_bot_heartbeats (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        bot_name VARCHAR(120) NOT NULL DEFAULT '',
                        bot_version VARCHAR(64) NOT NULL DEFAULT '',
                        guild_id VARCHAR(64) NOT NULL DEFAULT '',
                        last_seen_at DATETIME NOT NULL,
                        extra_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_hb_league_seen "
                    "ON discord_bot_heartbeats (league_slug, last_seen_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_hb_bot "
                    "ON discord_bot_heartbeats (bot_name)"
                )
            )
        has_dm = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_direct_message_events'")
        ).fetchone()
        if not has_dm:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_direct_message_events (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        recipient_user_id INTEGER NOT NULL,
                        discord_user_id VARCHAR(32) NOT NULL,
                        event_key VARCHAR(64) NOT NULL,
                        source_type VARCHAR(64) NOT NULL DEFAULT '',
                        source_id VARCHAR(64) NOT NULL DEFAULT '',
                        idempotency_key VARCHAR(64) NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        status VARCHAR(24) NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at DATETIME NOT NULL,
                        next_attempt_at DATETIME,
                        sent_at DATETIME,
                        discord_channel_id VARCHAR(32) NOT NULL DEFAULT '',
                        discord_message_id VARCHAR(32) NOT NULL DEFAULT ''
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_dm_status_created "
                    "ON discord_direct_message_events (status, created_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_dm_league_status "
                    "ON discord_direct_message_events (league_slug, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_dm_recipient_status "
                    "ON discord_direct_message_events (recipient_user_id, status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_dm_idempotency_key "
                    "ON discord_direct_message_events (idempotency_key)"
                )
            )
        else:
            dm_cols = conn.execute(text("PRAGMA table_info(discord_direct_message_events)")).fetchall()
            dm_names = {str(c[1]) for c in dm_cols}
            for col_name, ddl in {
                "source_type": "ALTER TABLE discord_direct_message_events ADD COLUMN source_type VARCHAR(64) NOT NULL DEFAULT ''",
                "source_id": "ALTER TABLE discord_direct_message_events ADD COLUMN source_id VARCHAR(64) NOT NULL DEFAULT ''",
                "idempotency_key": "ALTER TABLE discord_direct_message_events ADD COLUMN idempotency_key VARCHAR(64) NOT NULL DEFAULT ''",
                "next_attempt_at": "ALTER TABLE discord_direct_message_events ADD COLUMN next_attempt_at DATETIME",
                "discord_channel_id": "ALTER TABLE discord_direct_message_events ADD COLUMN discord_channel_id VARCHAR(32) NOT NULL DEFAULT ''",
                "discord_message_id": "ALTER TABLE discord_direct_message_events ADD COLUMN discord_message_id VARCHAR(32) NOT NULL DEFAULT ''",
            }.items():
                if col_name not in dm_names:
                    conn.execute(text(ddl))
        if has_routes:
            route_cols = conn.execute(text("PRAGMA table_info(discord_channel_routes)")).fetchall()
            route_names = {str(c[1]) for c in route_cols}
            if "discord_channel_id" not in route_names:
                conn.execute(
                    text(
                        "ALTER TABLE discord_channel_routes "
                        "ADD COLUMN discord_channel_id VARCHAR(32) NOT NULL DEFAULT ''"
                    )
                )
            if "label" not in route_names:
                conn.execute(
                    text(
                        "ALTER TABLE discord_channel_routes "
                        "ADD COLUMN label VARCHAR(120) NOT NULL DEFAULT ''"
                    )
                )
            if "description" not in route_names:
                conn.execute(
                    text("ALTER TABLE discord_channel_routes ADD COLUMN description TEXT NOT NULL DEFAULT ''")
                )
        has_bot_cfg = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_league_bot_config'")
        ).fetchone()
        if not has_bot_cfg:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_league_bot_config (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        guild_id VARCHAR(64) NOT NULL DEFAULT '',
                        gm_role_id VARCHAR(64) NOT NULL DEFAULT '',
                        is_enabled BOOLEAN NOT NULL DEFAULT 1,
                        notes TEXT NOT NULL DEFAULT '',
                        suppressed_default_route_keys_json TEXT NOT NULL DEFAULT '[]',
                        updated_by_user_id INTEGER,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_discord_bot_cfg_league "
                    "ON discord_league_bot_config (league_slug)"
                )
            )
        else:
            cfg_cols = conn.execute(text("PRAGMA table_info(discord_league_bot_config)")).fetchall()
            cfg_col_names = {str(c[1]) for c in cfg_cols}
            if "suppressed_default_route_keys_json" not in cfg_col_names:
                conn.execute(
                    text(
                        "ALTER TABLE discord_league_bot_config "
                        "ADD COLUMN suppressed_default_route_keys_json TEXT NOT NULL DEFAULT '[]'"
                    )
                )
            if "gm_role_id" not in cfg_col_names:
                conn.execute(
                    text(
                        "ALTER TABLE discord_league_bot_config "
                        "ADD COLUMN gm_role_id VARCHAR(64) NOT NULL DEFAULT ''"
                    )
                )
        has_delivered = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discord_delivered_sources'")
        ).fetchone()
        if not has_delivered:
            conn.execute(
                text(
                    """
                    CREATE TABLE discord_delivered_sources (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        source_type VARCHAR(64) NOT NULL,
                        source_id VARCHAR(64) NOT NULL,
                        event_key VARCHAR(64) NOT NULL DEFAULT '',
                        outbound_event_id INTEGER,
                        delivered_at DATETIME NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_discord_delivered_source "
                    "ON discord_delivered_sources (league_slug, source_type, source_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_discord_delivered_league "
                    "ON discord_delivered_sources (league_slug, delivered_at)"
                )
            )
        conn.commit()


def ensure_bowl_six_slates_discord_columns_sqlite(engine: Engine) -> None:
    """Add post-launch BOWL Six slate columns on site DB (SQLite or MySQL)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    if not insp.has_table("bowl_six_slates"):
        return
    cols = {str(col["name"]) for col in insp.get_columns("bowl_six_slates")}
    alters: list[str] = []
    if "scoring_week_start" not in cols:
        alters.append("ALTER TABLE bowl_six_slates ADD COLUMN scoring_week_start DATE")
    if "scoring_week_end" not in cols:
        alters.append("ALTER TABLE bowl_six_slates ADD COLUMN scoring_week_end DATE")
    if "discord_leaders_message_id" not in cols:
        alters.append(
            "ALTER TABLE bowl_six_slates ADD COLUMN discord_leaders_message_id VARCHAR(32)"
        )
    if "discord_leaders_channel_id" not in cols:
        alters.append(
            "ALTER TABLE bowl_six_slates ADD COLUMN discord_leaders_channel_id VARCHAR(32)"
        )
    if "discord_leaders_payload_hash" not in cols:
        alters.append(
            "ALTER TABLE bowl_six_slates ADD COLUMN discord_leaders_payload_hash VARCHAR(64)"
        )
    if alters:
        with engine.begin() as conn:
            for sql in alters:
                conn.execute(text(sql))

    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_bowl_six_slate_auto_update "
                "ON bowl_six_slates (league_slug, status, week_end)"
            )
        )
        lineups_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bowl_six_lineups'")
        ).fetchone()
        if lineups_exists:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_bowl_six_lineup_slate_submitted "
                    "ON bowl_six_lineups (slate_id, submitted_at)"
                )
            )
        player_stats_exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='bowl_six_player_week_stats'"
            )
        ).fetchone()
        if player_stats_exists:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_bowl_six_player_week_slate_pts "
                    "ON bowl_six_player_week_stats (slate_id, fantasy_points)"
                )
            )
        conn.commit()


def ensure_bowl_six_game_finals_sqlite(engine: Engine) -> None:
    """Create BOWL Six real-time game-final tracking table on site DB."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bowl_six_game_finals'")
        ).fetchone()
        if not exists:
            conn.execute(
                text(
                    """
                    CREATE TABLE bowl_six_game_finals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        league_slug VARCHAR(64) NOT NULL,
                        game_id INTEGER NOT NULL,
                        season_id INTEGER,
                        fhm_game_id VARCHAR(64),
                        first_final_at DATETIME NOT NULL,
                        CONSTRAINT uq_bowl_six_game_final_league_game
                            UNIQUE (league_slug, game_id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_bowl_six_game_final_league_seen "
                    "ON bowl_six_game_finals (league_slug, first_final_at)"
                )
            )
        conn.commit()


def ensure_prospect_system_rank_snapshots_sqlite(engine: Engine) -> None:
    """Create prospect system rank snapshot table on site DB when missing."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prospect_system_rank_snapshots'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE prospect_system_rank_snapshots (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    snapshot_at DATETIME NOT NULL,
                    ranks_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_prospect_sys_snap_league_at "
                "ON prospect_system_rank_snapshots (league_slug, snapshot_at)"
            )
        )
        conn.commit()


def ensure_league_draft_slot_boost_tier_sqlite(engine: Engine) -> None:
    """Add newer Draft Hub setup columns when missing (site DB, SQLite).

    Slot tier values: '' (default), 'gold', or 'silver' — set by admin after the boost lottery
    so the public Draft Hub page can highlight those overall picks. Original team tracks draft-day
    trades separately from the current pick holder. Penalty picks are red-highlighted commissioner
    setup flags.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        draft_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='league_drafts'")
        ).fetchone()
        if draft_exists:
            draft_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(league_drafts)"))}
            if "picks_per_round" not in draft_cols:
                conn.execute(
                    text("ALTER TABLE league_drafts ADD COLUMN picks_per_round INTEGER NOT NULL DEFAULT 27")
                )
            if "timer_paused" not in draft_cols:
                conn.execute(
                    text("ALTER TABLE league_drafts ADD COLUMN timer_paused BOOLEAN NOT NULL DEFAULT 0")
                )
            if "timer_paused_remaining_seconds" not in draft_cols:
                conn.execute(
                    text("ALTER TABLE league_drafts ADD COLUMN timer_paused_remaining_seconds INTEGER")
                )
            if "gm_picks_enabled" not in draft_cols:
                conn.execute(
                    text("ALTER TABLE league_drafts ADD COLUMN gm_picks_enabled BOOLEAN NOT NULL DEFAULT 0")
                )
            if "discord_on_deck_enabled" not in draft_cols:
                conn.execute(
                    text(
                        "ALTER TABLE league_drafts ADD COLUMN discord_on_deck_enabled BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            if "eligibility_pool_source" not in draft_cols:
                conn.execute(
                    text(
                        "ALTER TABLE league_drafts "
                        "ADD COLUMN eligibility_pool_source VARCHAR(32) NOT NULL DEFAULT 'age_rules'"
                    )
                )
            if "born_before_date" not in draft_cols:
                conn.execute(text("ALTER TABLE league_drafts ADD COLUMN born_before_date DATE"))

        slot_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='league_draft_slots'")
        ).fetchone()
        if slot_exists:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(league_draft_slots)"))}
            if "original_team_id" not in cols:
                conn.execute(text("ALTER TABLE league_draft_slots ADD COLUMN original_team_id INTEGER"))
                conn.execute(
                    text(
                        "UPDATE league_draft_slots "
                        "SET original_team_id = team_id "
                        "WHERE original_team_id IS NULL"
                    )
                )
            if "boost_tier" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE league_draft_slots "
                        "ADD COLUMN boost_tier VARCHAR(16) NOT NULL DEFAULT ''"
                    )
                )
            if "penalty_pick" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE league_draft_slots "
                        "ADD COLUMN penalty_pick BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
        conn.commit()


def ensure_boost_lottery_team_results_sqlite(engine: Engine) -> None:
    """Create admin-maintained Boost Lottery winner totals on the site DB."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='boost_lottery_team_results'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE boost_lottery_team_results (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    team_id INTEGER NOT NULL,
                    gold_count INTEGER NOT NULL DEFAULT 0,
                    silver_count INTEGER NOT NULL DEFAULT 0,
                    updated_by_user_id INTEGER,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_boost_lottery_team_league_team "
                "ON boost_lottery_team_results (league_slug, team_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_boost_lottery_team_league "
                "ON boost_lottery_team_results (league_slug, team_id)"
            )
        )
        conn.commit()


def ensure_game_record_baselines_sqlite(engine: Engine) -> None:
    """Create admin-seeded single-game record baselines table (league DB)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_record_baselines'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE game_record_baselines (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    metric_key VARCHAR(64) NOT NULL,
                    segment VARCHAR(8) NOT NULL DEFAULT 'rs',
                    scope VARCHAR(16) NOT NULL DEFAULT 'all',
                    player_kind VARCHAR(16) NOT NULL DEFAULT 'skater',
                    value FLOAT NOT NULL,
                    player_id INTEGER,
                    team_id INTEGER,
                    opponent_team_id INTEGER,
                    game_id INTEGER,
                    game_date DATE,
                    season_label VARCHAR(32),
                    notes TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players (id),
                    FOREIGN KEY(team_id) REFERENCES teams (id),
                    FOREIGN KEY(opponent_team_id) REFERENCES teams (id),
                    FOREIGN KEY(game_id) REFERENCES games (id),
                    CONSTRAINT uq_game_record_baseline_metric UNIQUE (
                        metric_key, segment, scope, player_kind
                    )
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_game_record_baseline_segment "
                "ON game_record_baselines (segment, scope, player_kind)"
            )
        )
        conn.commit()


def ensure_gm_export_attendance_sqlite(engine: Engine) -> None:
    """Create GM export attendance tracker table on the site DB."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='gm_export_attendance'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE gm_export_attendance (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    team_id INTEGER NOT NULL,
                    export_date DATE NOT NULL,
                    checked_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    ap_ledger_entry_id INTEGER,
                    previous_export_date DATE,
                    gap_days INTEGER,
                    gap_warning_sent_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_gm_export_attendance_team_date "
                "ON gm_export_attendance (league_slug, team_id, export_date)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_export_attendance_league_date "
                "ON gm_export_attendance (league_slug, export_date)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_export_attendance_team "
                "ON gm_export_attendance (league_slug, team_id)"
            )
        )
        conn.commit()


def ensure_sim_cycle_state_sqlite(engine: Engine) -> None:
    """Create sim cycle tracker state table on the site DB."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='sim_cycle_state'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE sim_cycle_state (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    phase VARCHAR(16) NOT NULL DEFAULT 'idle',
                    export_date DATE,
                    cycle_started_at DATETIME,
                    updated_at DATETIME NOT NULL,
                    discord_message_id VARCHAR(32),
                    discord_channel_id VARCHAR(32),
                    discord_payload_hash VARCHAR(64),
                    tracker_last_message_id VARCHAR(32),
                    tracker_bot_user_id VARCHAR(32),
                    live_exported_fhm_team_ids_json TEXT NOT NULL DEFAULT '[]',
                    finalize_on_ack BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_sim_cycle_state_league "
                "ON sim_cycle_state (league_slug)"
            )
        )
        conn.commit()


def ensure_gm_rule_strikes_sqlite(engine: Engine) -> None:
    """Create cap strike-tracking table on the site DB."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='gm_rule_strikes'"
            )
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE gm_rule_strikes (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    league_slug VARCHAR(64) NOT NULL,
                    cycle_year INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    strike_no INTEGER NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_gm_rule_strike_cycle_team_no "
                "ON gm_rule_strikes (league_slug, cycle_year, team_id, strike_no)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_gm_rule_strike_cycle "
                "ON gm_rule_strikes (league_slug, cycle_year, team_id)"
            )
        )
        conn.commit()


def ensure_league_expansion_draft_columns_sqlite(engine: Engine) -> None:
    """Add expansion draft commissioner fields when missing (site DB, SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='league_expansion_drafts'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(league_expansion_drafts)"))}
        if "expansion_team_count" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE league_expansion_drafts "
                    "ADD COLUMN expansion_team_count INTEGER NOT NULL DEFAULT 1"
                )
            )
        if "goalie_phase_first_team_id" not in cols:
            conn.execute(
                text("ALTER TABLE league_expansion_drafts ADD COLUMN goalie_phase_first_team_id INTEGER")
            )
        if "skater_phase_first_team_id" not in cols:
            conn.execute(
                text("ALTER TABLE league_expansion_drafts ADD COLUMN skater_phase_first_team_id INTEGER")
            )
        if "expansion_pick_cooldown_active" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE league_expansion_drafts "
                    "ADD COLUMN expansion_pick_cooldown_active BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        conn.commit()


def ensure_mobile_push_devices_sqlite(engine: Engine) -> None:
    """Store FCM/APNs registration tokens per user and league (site DB, SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_push_devices'")
        ).fetchone()
        if exists:
            return
        conn.execute(
            text(
                """
                CREATE TABLE mobile_push_devices (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    league_slug VARCHAR(64) NOT NULL,
                    platform VARCHAR(16) NOT NULL,
                    device_token TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_mobile_push_user_league_platform
                        UNIQUE (user_id, league_slug, platform)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_mobile_push_league_user "
                "ON mobile_push_devices (league_slug, user_id)"
            )
        )
        conn.commit()
