"""SQLite FTS5 helpers and post-migration setup."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


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


def ensure_fts5(engine: Engine) -> None:
    """Create the player search virtual table if missing."""
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS player_fts USING fts5(
                    full_name,
                    position,
                    team_abbrev,
                    player_id UNINDEXED,
                    tokenize = "unicode61 remove_diacritics 2"
                );
                """
            )
        )
        conn.commit()


def rebuild_player_fts(engine: Engine) -> None:
    """Rebuild FTS index from players + current team."""
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM player_fts;"))
        conn.execute(
            text(
                """
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
            )
        )
        conn.commit()


def repair_fhm_team_city_from_name(engine: Engine) -> None:
    """Set ``teams.city`` to match ``teams.name`` for FHM imports (city was wrongly ``name.split()[0]``)."""
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                UPDATE teams
                SET city = name
                WHERE fhm_team_id IS NOT NULL
                  AND name IS NOT NULL
                  AND TRIM(name) != ''
                """
            )
        )
        conn.commit()
