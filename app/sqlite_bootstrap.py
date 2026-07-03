"""Coordinate SQLite schema bootstrap across WSGI workers and league mounts."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import is_sqlite_database_uri
from app.sqlite_bootstrap_lock import sqlite_bootstrap_lock

if TYPE_CHECKING:
    from flask import Flask

# Bump when ensure_* migrations in bootstrap_league_sqlite or bootstrap_site_sqlite change.
SQLITE_BOOTSTRAP_VERSION = 1

_completed_in_process: set[str] = set()
_server_site_bootstrapped = False
_server_site_bootstrap_lock = threading.Lock()
_log = logging.getLogger(__name__)


def _db_key(db_uri: str) -> str | None:
    if not isinstance(db_uri, str) or not db_uri.startswith("sqlite:///"):
        return None
    raw = db_uri[len("sqlite:///") :]
    if not raw:
        return None
    return str(Path(raw).resolve())


def _marker_path(db_key: str) -> Path:
    return Path(db_key + ".bootstrap.version")


def _marker_matches(db_key: str) -> bool:
    marker = _marker_path(db_key)
    db_path = Path(db_key)
    try:
        if marker.read_text(encoding="utf-8").strip() != str(SQLITE_BOOTSTRAP_VERSION):
            return False
        # DB restored from backup is usually older than the marker — re-run bootstrap/migrations.
        if db_path.is_file() and marker.stat().st_mtime > db_path.stat().st_mtime + 1:
            return False
        return True
    except OSError:
        return False


def _write_marker(db_key: str) -> None:
    marker = _marker_path(db_key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{SQLITE_BOOTSTRAP_VERSION}\n", encoding="utf-8")


def run_sqlite_bootstrap_once(
    db_uri: str,
    bootstrap: Callable[[], None],
    *,
    timeout_s: float = 5.0,
    label: str = "",
) -> None:
    """Run bootstrap under a cross-process lock, skipping when already complete."""
    db_key = _db_key(db_uri)
    if db_key is None:
        bootstrap()
        return

    if db_key in _completed_in_process:
        return

    try:
        with sqlite_bootstrap_lock(db_uri, timeout_s=timeout_s):
            if db_key in _completed_in_process:
                return
            if _marker_matches(db_key):
                _completed_in_process.add(db_key)
                return
            bootstrap()
            _write_marker(db_key)
            _completed_in_process.add(db_key)
            if label:
                _log.info("SQLite bootstrap complete for %s", label)
    except TimeoutError:
        _completed_in_process.add(db_key)
        _log.warning(
            "SQLite bootstrap skipped for %s because another worker holds the lock",
            label or db_key,
        )


def apply_league_sqlite_migrations(app: Flask) -> None:
    """Idempotent league schema patches (safe after backup restore)."""
    from app.db_utils import (
        ensure_advanced_stats_columns_sqlite,
        ensure_franchise_team_identities_sqlite,
        ensure_fts5,
        ensure_game_record_baselines_sqlite,
        ensure_history_all_stars_sqlite,
        ensure_history_awards_staff_fhm_id_sqlite,
        ensure_history_records_admin_metadata_sqlite,
        ensure_homepage_performance_indexes_sqlite,
        ensure_player_goalie_stats_gsaa_sqlite,
        ensure_player_overall_baseline_sqlite,
        ensure_player_rating_snapshots_sqlite,
        ensure_players_boost_tier_sqlite,
        ensure_players_jersey_number_sqlite,
        ensure_skater_career_line_career_source_sqlite,
        ensure_skater_career_line_extra_stats_sqlite,
        ensure_skater_career_line_game_rating_sqlite,
        ensure_team_honors_meta_sqlite,
        ensure_team_retired_numbers_sqlite,
        ensure_team_season_aggregate_extra_columns,
        ensure_team_victory_banners_sqlite,
        migrate_team_season_aggregates_sqlite,
        repair_fhm_team_city_from_name,
    )
    from app.models import db

    migrate_team_season_aggregates_sqlite(db.engine)
    repair_fhm_team_city_from_name(db.engine)
    ensure_players_jersey_number_sqlite(db.engine)
    ensure_players_boost_tier_sqlite(db.engine)
    ensure_player_overall_baseline_sqlite(db.engine)
    ensure_player_rating_snapshots_sqlite(db.engine)
    ensure_team_season_aggregate_extra_columns(db.engine)
    ensure_homepage_performance_indexes_sqlite(db.engine)
    ensure_skater_career_line_career_source_sqlite(db.engine)
    ensure_skater_career_line_extra_stats_sqlite(db.engine)
    ensure_skater_career_line_game_rating_sqlite(db.engine)
    ensure_player_goalie_stats_gsaa_sqlite(db.engine)
    ensure_advanced_stats_columns_sqlite(db.engine)
    ensure_game_record_baselines_sqlite(db.engine)
    ensure_history_awards_staff_fhm_id_sqlite(db.engine)
    ensure_history_records_admin_metadata_sqlite(db.engine)
    ensure_history_all_stars_sqlite(db.engine)
    ensure_franchise_team_identities_sqlite(db.engine)
    ensure_team_honors_meta_sqlite(db.engine)
    ensure_team_retired_numbers_sqlite(db.engine)
    ensure_team_victory_banners_sqlite(db.engine)
    ensure_fts5(db.engine)


def bootstrap_league_sqlite(app: Flask) -> None:
    """League DB schema/migrations only (never holds the site DB lock)."""
    from sqlalchemy import func, select

    from app.config import Config
    from app.models import FranchiseTeamIdentity, db
    from app.services.franchise_identities import seed_franchise_identities_from_csv

    db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if not db_uri.startswith("sqlite:///"):
        return

    slug = str(app.config.get("LEAGUE_SLUG") or "?")
    db_path = Path(db_uri.replace("sqlite:///", "", 1))
    if db_path.is_file():
        from app.db_utils import prepare_sqlite_database

        healthy, health_msg = prepare_sqlite_database(db_path, auto_repair=True)
        if not healthy:
            app.logger.error(
                "League %s SQLite unhealthy at %s (%s). "
                "Run: python scripts/repair_league_sqlite.py --repair --league %s",
                slug,
                db_path,
                health_msg,
                slug,
            )

    def _bootstrap() -> None:
        db.create_all()
        try:
            identity_count = db.session.scalar(select(func.count()).select_from(FranchiseTeamIdentity)) or 0
            identity_csv = Path(app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "team_identity_history.csv"
            if int(identity_count) == 0 and identity_csv.is_file():
                seed_franchise_identities_from_csv(db.session, identity_csv)
                db.session.commit()
        except Exception:
            db.session.rollback()

    run_sqlite_bootstrap_once(db_uri, _bootstrap, label=f"league {slug}")
    try:
        apply_league_sqlite_migrations(app)
    except Exception as exc:
        app.logger.warning("League SQLite migrations skipped for %s: %s", slug, exc)


def _bootstrap_site_schema_and_seed(app: Flask) -> None:
    from app.db_utils import (
        ensure_admin_undo_actions_sqlite,
        ensure_awards_voting_sqlite,
        ensure_boost_lottery_team_results_sqlite,
        ensure_bowl_six_game_finals_sqlite,
        ensure_bowl_six_slates_discord_columns_sqlite,
        ensure_discord_outbound_sqlite,
        ensure_gm_approval_requests_sqlite,
        ensure_gm_export_attendance_sqlite,
        ensure_gm_rule_strikes_sqlite,
        ensure_sim_cycle_state_sqlite,
        ensure_gm_trade_proposals_sqlite,
        ensure_homepage_module_settings_sqlite,
        ensure_league_draft_slot_boost_tier_sqlite,
        ensure_league_expansion_draft_columns_sqlite,
        ensure_league_rule_settings_sqlite,
        ensure_member_watchlists_sqlite,
        ensure_news_engagement_sqlite,
        ensure_password_reset_tokens_sqlite,
        ensure_positional_rank_snapshots_sqlite,
        ensure_power_rank_snapshots_sqlite,
        ensure_prospect_league_rank_snapshots_sqlite,
        ensure_prospect_system_rank_snapshots_sqlite,
        ensure_rfa_offer_requests_sqlite,
        ensure_site_announcements_sqlite,
        ensure_site_banned_identities_sqlite,
        ensure_site_users_admin_role_sqlite,
        ensure_staff_change_requests_sqlite,
        ensure_story_publish_schedule_extra_columns_sqlite,
        ensure_story_publish_schedules_sqlite,
        ensure_team_cap_penalties_sqlite,
        ensure_team_staff_budget_current_salary_sqlite,
        ensure_team_staff_roster_entries_sqlite,
        ensure_trade_market_sqlite,
    )
    from app.models import db
    from app.services.ap_service import seed_ap_catalog_if_empty

    db.create_all(bind_key="site")
    try:
        site_engine = db.engines.get("site")
    except Exception:
        site_engine = None
    if site_engine is None:
        return
    ensure_homepage_module_settings_sqlite(site_engine)
    ensure_site_announcements_sqlite(site_engine)
    ensure_site_users_admin_role_sqlite(site_engine)
    ensure_password_reset_tokens_sqlite(site_engine)
    ensure_site_banned_identities_sqlite(site_engine)
    ensure_league_rule_settings_sqlite(site_engine)
    ensure_gm_approval_requests_sqlite(site_engine)
    ensure_staff_change_requests_sqlite(site_engine)
    ensure_rfa_offer_requests_sqlite(site_engine)
    ensure_team_cap_penalties_sqlite(site_engine)
    ensure_team_staff_budget_current_salary_sqlite(site_engine)
    ensure_team_staff_roster_entries_sqlite(site_engine)
    ensure_gm_trade_proposals_sqlite(site_engine)
    ensure_trade_market_sqlite(site_engine)
    ensure_story_publish_schedules_sqlite(site_engine)
    ensure_story_publish_schedule_extra_columns_sqlite(site_engine)
    ensure_awards_voting_sqlite(site_engine)
    ensure_member_watchlists_sqlite(site_engine)
    ensure_news_engagement_sqlite(site_engine)
    ensure_admin_undo_actions_sqlite(site_engine)
    ensure_bowl_six_slates_discord_columns_sqlite(site_engine)
    ensure_bowl_six_game_finals_sqlite(site_engine)
    ensure_discord_outbound_sqlite(site_engine)
    try:
        from sqlalchemy.orm import Session

        from app.services.discord_events import bootstrap_discord_integration_all_leagues

        with Session(site_engine) as site_session:
            bootstrap_discord_integration_all_leagues(site_session)
    except Exception as exc:
        app.logger.warning("Discord integration bootstrap skipped: %s", exc)
    ensure_prospect_system_rank_snapshots_sqlite(site_engine)
    ensure_positional_rank_snapshots_sqlite(site_engine)
    ensure_power_rank_snapshots_sqlite(site_engine)
    ensure_prospect_league_rank_snapshots_sqlite(site_engine)
    ensure_league_draft_slot_boost_tier_sqlite(site_engine)
    ensure_boost_lottery_team_results_sqlite(site_engine)
    ensure_gm_export_attendance_sqlite(site_engine)
    ensure_sim_cycle_state_sqlite(site_engine)
    ensure_gm_rule_strikes_sqlite(site_engine)
    ensure_league_expansion_draft_columns_sqlite(site_engine)
    seed_ap_catalog_if_empty()


def bootstrap_site_database(app: Flask) -> None:
    """Shared site DB bootstrap (SQLite file lock or one-time per worker on MySQL)."""
    site_uri = str(app.config.get("SITE_SQLALCHEMY_DATABASE_URI") or "").strip()
    if not site_uri:
        return

    if is_sqlite_database_uri(site_uri):
        site_path = Path(site_uri.replace("sqlite:///", "", 1))
        if site_path.is_file():
            from app.db_utils import prepare_sqlite_database

            healthy, health_msg = prepare_sqlite_database(site_path, auto_repair=True)
            if not healthy:
                app.logger.error(
                    "Site SQLite unhealthy at %s (%s). "
                    "Run: python scripts/repair_league_sqlite.py --repair --league site",
                    site_path,
                    health_msg,
                )
        run_sqlite_bootstrap_once(
            site_uri,
            lambda: _bootstrap_site_schema_and_seed(app),
            label="site membership",
        )
        return

    global _server_site_bootstrapped
    with _server_site_bootstrap_lock:
        if _server_site_bootstrapped:
            return
        _bootstrap_site_schema_and_seed(app)
        _server_site_bootstrapped = True
        _log.info("Site database bootstrap complete for server DB")


def bootstrap_site_sqlite(app: Flask) -> None:
    """Backward-compatible alias."""
    bootstrap_site_database(app)
