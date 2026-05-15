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
    """Add site_users.admin_role when missing (site DB, SQLite)."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_users'")
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(site_users)"))}
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
    trades separately from the current pick holder.
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
