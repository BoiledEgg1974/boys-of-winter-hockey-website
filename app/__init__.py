import colorsys
import importlib
from pathlib import Path

import click
from flask import Flask, session
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from app.auth_login import login_manager
from app.config import LEAGUES, Config, install_shared_site_mysql_engine, site_bind_engine_config
from app.db_utils import (
    ensure_fts5,
    ensure_history_all_stars_sqlite,
    ensure_history_awards_staff_fhm_id_sqlite,
    ensure_history_records_admin_metadata_sqlite,
    ensure_boost_lottery_team_results_sqlite,
    ensure_homepage_performance_indexes_sqlite,
    ensure_players_boost_tier_sqlite,
    ensure_game_record_baselines_sqlite,
    ensure_record_leader_snapshots_sqlite,
    ensure_record_stat_adjustments_sqlite,
    ensure_gm_export_attendance_sqlite,
    ensure_gm_rule_strikes_sqlite,
    ensure_players_jersey_number_sqlite,
    ensure_player_overall_baseline_sqlite,
    ensure_player_rating_snapshots_sqlite,
    ensure_player_rating_snapshot_timeline_columns_sqlite,
    ensure_player_analytics_snapshots_sqlite,
    ensure_team_analytics_snapshots_sqlite,
    ensure_advanced_stats_hub_snapshots_sqlite,
    ensure_org_development_report_archives_sqlite,
    ensure_homepage_module_settings_sqlite,
    ensure_league_draft_slot_boost_tier_sqlite,
    ensure_league_expansion_draft_columns_sqlite,
    ensure_site_announcements_sqlite,
    ensure_site_users_admin_role_sqlite,
    ensure_password_reset_tokens_sqlite,
    ensure_site_banned_identities_sqlite,
    ensure_league_rule_settings_sqlite,
    ensure_gm_approval_requests_sqlite,
    ensure_staff_change_requests_sqlite,
    ensure_rfa_offer_requests_sqlite,
    ensure_team_staff_roster_entries_sqlite,
    ensure_gm_trade_proposals_sqlite,
    ensure_trade_market_sqlite,
    ensure_story_publish_schedules_sqlite,
    ensure_story_publish_schedule_extra_columns_sqlite,
    ensure_awards_voting_sqlite,
    ensure_member_watchlists_sqlite,
    ensure_franchise_team_identities_sqlite,
    ensure_team_honors_meta_sqlite,
    ensure_team_retired_numbers_sqlite,
    ensure_team_victory_banners_sqlite,
    ensure_mobile_push_devices_sqlite,
    ensure_news_engagement_sqlite,
    ensure_admin_undo_actions_sqlite,
    ensure_bowl_six_game_finals_sqlite,
    ensure_bowl_six_slates_discord_columns_sqlite,
    ensure_discord_outbound_sqlite,
    ensure_prospect_system_rank_snapshots_sqlite,
    ensure_positional_rank_snapshots_sqlite,
    ensure_power_rank_snapshots_sqlite,
    ensure_prospect_league_rank_snapshots_sqlite,
    ensure_skater_career_line_career_source_sqlite,
    ensure_skater_career_line_extra_stats_sqlite,
    ensure_skater_career_line_game_rating_sqlite,
    ensure_player_goalie_stats_gsaa_sqlite,
    ensure_advanced_stats_columns_sqlite,
    ensure_team_season_aggregate_extra_columns,
    migrate_team_season_aggregates_sqlite,
    rebuild_player_fts,
    repair_fhm_team_city_from_name,
)
from app.models import Player, Team, db
from app.sqlite_bootstrap import bootstrap_league_sqlite, bootstrap_site_database
from app.sqlite_pragmas import install_sqlite_connect_pragmas

csrf = CSRFProtect()
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.roster_team import main_league_roster_team


def create_app(config_class: type = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_relative_config=True,
    )
    app.config.from_object(config_class)

    @app.before_request
    def _idle_timeout_touch_session():
        # Sliding idle timeout for authenticated users (default 30 minutes).
        if getattr(current_user, "is_authenticated", False):
            session.permanent = True
            session.modified = True

    site_uri = app.config.get("SITE_SQLALCHEMY_DATABASE_URI")
    if site_uri:
        binds = dict(app.config.get("SQLALCHEMY_BINDS") or {})
        binds["site"] = site_bind_engine_config(str(site_uri))
        app.config["SQLALCHEMY_BINDS"] = binds

    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    for sub in (
        config_class.RAW_IMPORT_DIR,
        config_class.TEAM_LOGOS_DIR,
        config_class.LEAGUE_LOGO_DIR,
        config_class.HISTORY_CHAMPIONS_DIR,
        config_class.PLAYER_HEADSHOTS_DIR,
    ):
        Path(sub).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    install_shared_site_mysql_engine(db, app)
    install_sqlite_connect_pragmas()
    csrf.init_app(app)
    login_manager.init_app(app)

    importlib.import_module("app.site_models")
    from app.config import is_racing_league as _is_racing_league_cfg

    if _is_racing_league_cfg(str(app.config.get("LEAGUE_SLUG") or "")):
        importlib.import_module("app.racing_models")

    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if isinstance(db_uri, str) and db_uri.startswith("sqlite:///"):
        app.logger.info(
            "League %s using SQLite %s",
            app.config.get("LEAGUE_SLUG", "?"),
            db_uri.replace("sqlite:///", "", 1),
        )

    with app.app_context():
        bootstrap_league_sqlite(app)
        bootstrap_site_database(app)
        try:
            site_engine = db.engines.get("site")
            if site_engine is not None:
                from app.db_utils import (
                    ensure_bowl_six_slates_discord_columns_sqlite,
                    ensure_discord_playoff_bracket_sqlite,
                    ensure_team_cap_penalties_sqlite,
                    ensure_team_staff_budget_current_salary_sqlite,
                )

                ensure_team_cap_penalties_sqlite(site_engine)
                ensure_team_staff_budget_current_salary_sqlite(site_engine)
                ensure_discord_playoff_bracket_sqlite(site_engine)
                ensure_bowl_six_slates_discord_columns_sqlite(site_engine)
        except Exception as exc:
            app.logger.warning("site DB schema ensure skipped: %s", exc)

        from app.config import is_racing_league as _is_racing_league_boot

        _boot_slug = str(app.config.get("LEAGUE_SLUG") or "")
        if not _is_racing_league_boot(_boot_slug):
            # FTS may be empty until import or seed; seed script calls rebuild
            try:
                from app.services.ratings_position_cache import backfill_null_positions_from_ratings

                n = backfill_null_positions_from_ratings(db.session)
                if n:
                    app.logger.info(
                        "Backfilled player.position from player_ratings.csv for %s players (was NULL)",
                        n,
                    )
            except Exception as exc:
                app.logger.warning("Position backfill from ratings skipped: %s", exc)

            try:
                from app.services.player_ability_potential import (
                    backfill_missing_ability_potential_from_ratings,
                )

                n = backfill_missing_ability_potential_from_ratings(db.session)
                if n:
                    app.logger.info(
                        "Backfilled player ABI/POT from player_ratings.csv for %s players (was NULL)",
                        n,
                    )
            except Exception as exc:
                app.logger.warning("Ability/potential backfill from ratings skipped: %s", exc)

            try:
                from app.sqlite_retry import commit_with_sqlite_retry
                from app.services.player_boost_markers import sync_player_boost_markers

                counts = sync_player_boost_markers(db.session)
                if counts["seeded"] or counts["applied"]:
                    commit_with_sqlite_retry(db.session)
                    app.logger.info(
                        "Player boost markers synced for %s (seeded=%s applied=%s)",
                        _boot_slug,
                        counts["seeded"],
                        counts["applied"],
                    )
            except Exception as exc:
                app.logger.warning("Player boost marker sync skipped: %s", exc)

        try:
            from app.services.ap_service import seed_ap_catalog_if_empty

            seed_ap_catalog_if_empty()
        except Exception as exc:
            app.logger.warning("AP catalog seed skipped: %s", exc)

        try:
            from app.services.bootstrap_site import ensure_commish_admin

            ensure_commish_admin(app)
        except Exception as exc:
            app.logger.warning("Commissioner bootstrap skipped: %s", exc)

        if _is_racing_league_boot(_boot_slug):
            try:
                from app.services.racing_rewards import (
                    ensure_default_reward_tiers,
                    ensure_racing_reward_schema,
                )
                from app.sqlite_retry import commit_with_sqlite_retry

                ensure_racing_reward_schema(db.engine)
                db.create_all()
                ensure_default_reward_tiers(db.session, league_slug=_boot_slug)
                if _boot_slug == "bowl-formula":
                    from app.services.racing_import import refresh_pending_formula_race_ap_suggestions

                    refresh_pending_formula_race_ap_suggestions(db.session)
                commit_with_sqlite_retry(db.session)
            except Exception as exc:
                app.logger.warning("Racing reward schedule seed skipped: %s", exc)

    from sqlalchemy import select

    from app.config import is_racing_league
    from app.logo_urls import team_logo_url_for_team
    from app.models import Player, Team
    from app.routes import api_bp, main_bp
    from app.routes.draft_hub import draft_hub_bp
    from app.routes.expansion_draft_hub import expansion_draft_hub_bp
    from app.routes.site_portal import site_admin_bp, site_gm_bp

    _league_slug = str(app.config.get("LEAGUE_SLUG") or "")
    _racing = is_racing_league(_league_slug)

    if _racing:
        importlib.import_module("app.racing_models")
        from app.routes.racing import racing_bp

        app.register_blueprint(racing_bp)
        app.register_blueprint(api_bp, url_prefix="/api")
        csrf.exempt(api_bp)
        app.register_blueprint(site_admin_bp)
    else:
        from app.routes import bowl_six_portal as _bowl_six_portal  # noqa: F401 — routes on shared blueprints

        app.register_blueprint(main_bp)
        app.register_blueprint(draft_hub_bp)
        app.register_blueprint(expansion_draft_hub_bp)
        app.register_blueprint(api_bp, url_prefix="/api")
        csrf.exempt(api_bp)
        app.register_blueprint(site_gm_bp)
        app.register_blueprint(site_admin_bp)

    if app.config.get("LEAGUE_JSON_CACHE_WARM_ON_STARTUP", False):
        from app.services.homepage_summary_cache import warm_homepage_summary_cache

        warm_homepage_summary_cache(app)

    @app.template_filter("season_label_start_year")
    def season_label_start_year_filter(label: object) -> int | None:
        """First calendar year from a display label like ``1926–27`` (for era logo lookup)."""
        from app.services.league_season_records import _label_start_year

        return _label_start_year(str(label).strip() if label is not None else None)

    @app.template_filter("season_display")
    def season_display_filter(season: object) -> str:
        """Canonical Boys of Winter season label (July–June year) from ``Season.start_year`` when set."""
        from app.models import Season as SeasonModel
        from app.services.seasons import season_display_label

        if isinstance(season, SeasonModel):
            return season_display_label(season)
        return ""

    @app.template_filter("rating_pill_style")
    def rating_pill_style(val: object) -> str:
        """Inline CSS for ABI/POT pills: 0.5 dark red → 2.75 yellow → 5.0 blue (RGB then HSL blend)."""
        if val is None:
            return ""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        span = 5.0 - 0.5
        t = (v - 0.5) / span
        t = max(0.0, min(1.0, t))
        # Stops: dark red → yellow (t=0.5, value 2.75) → blue; yellow→blue in HSL for smooth hues
        r0, g0, b0 = 115, 22, 28
        r1, g1, b1 = 215, 175, 45
        r2, g2, b2 = 59, 130, 246
        if t <= 0.5:
            u = t / 0.5
            r = int(r0 + (r1 - r0) * u)
            g = int(g0 + (g1 - g0) * u)
            b = int(b0 + (b1 - b0) * u)
        else:
            u = (t - 0.5) / 0.5
            y = colorsys.rgb_to_hls(r1 / 255, g1 / 255, b1 / 255)
            bl = colorsys.rgb_to_hls(r2 / 255, g2 / 255, b2 / 255)
            dh = bl[0] - y[0]
            if dh > 0.5:
                dh -= 1.0
            elif dh < -0.5:
                dh += 1.0
            h = (y[0] + dh * u) % 1.0
            lum = y[1] + (bl[1] - y[1]) * u
            sat = y[2] + (bl[2] - y[2]) * u
            rr, gg, bb = colorsys.hls_to_rgb(h, lum, sat)
            r, g = int(rr * 255), int(gg * 255)
            b = int(bb * 255)
        br, bgc, bb = max(0, r - 28), max(0, g - 28), max(0, b - 28)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        fg = "#0f172a" if lum > 0.52 else "#f8fafc"
        return f"background-color:rgb({r},{g},{b});color:{fg};border-color:rgb({br},{bgc},{bb})"

    @app.template_filter("attr_rating_style")
    def attr_rating_style(val: object) -> str:
        """Same gradient as ABI/POT but for raw attributes on a 0–20 scale (maps onto 0.5–5.0)."""
        if val is None:
            return ""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        v = max(0.0, min(20.0, v))
        v_norm = 0.5 + (v / 20.0) * (5.0 - 0.5)
        return rating_pill_style(v_norm)

    @app.template_filter("attr_rating_text_style")
    def attr_rating_text_style(val: object) -> str:
        """Text color for 0–20 attributes: red→orange→yellow→green→blue."""
        if val is None:
            return ""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        v = max(0.0, min(20.0, v))
        stops = [
            (0.0, (220, 38, 38)),
            (8.0, (251, 146, 60)),
            (13.0, (190, 220, 80)),
            (16.0, (45, 212, 191)),
            (20.0, (59, 130, 246)),
        ]
        lo_idx = 0
        for i in range(1, len(stops)):
            if v <= stops[i][0]:
                lo_idx = i - 1
                break
            lo_idx = i - 1
        hi_idx = min(lo_idx + 1, len(stops) - 1)
        v0, c0 = stops[lo_idx]
        v1, c1 = stops[hi_idx]
        if v1 <= v0:
            t = 0.0
        else:
            t = (v - v0) / (v1 - v0)
        r = int(c0[0] + (c1[0] - c0[0]) * t)
        g = int(c0[1] + (c1[1] - c0[1]) * t)
        b = int(c0[2] + (c1[2] - c0[2]) * t)
        return f"color:rgb({r},{g},{b})"

    @app.template_filter("rating_meter_fill_style")
    def rating_meter_fill_style(val: object) -> str:
        """Width % and fill color for 0–21 horizontal rating bars (goalie panels)."""
        if val is None:
            return "width:0%;background-color:transparent"
        try:
            v = float(val)
        except (TypeError, ValueError):
            return "width:0%;background-color:transparent"
        v = max(0.0, min(21.0, v))
        pct = (v / 21.0) * 100.0
        if v >= 20:
            c = "rgb(59, 130, 246)"
        elif v >= 17:
            c = "rgb(34, 211, 238)"
        elif v >= 16:
            c = "rgb(45, 212, 191)"
        elif v >= 14:
            c = "rgb(132, 204, 22)"
        elif v >= 13:
            c = "rgb(190, 220, 80)"
        elif v >= 8:
            c = "rgb(251, 146, 60)"
        else:
            c = "rgb(220, 38, 38)"
        return f"width:{pct:.2f}%;background-color:{c};"

    @app.template_filter("nationality_flag_url")
    def nationality_flag_url_filter(nationality: object) -> str | None:
        from app.services.player_rating_avgs import flag_icon_url

        if nationality is None:
            return None
        return flag_icon_url(str(nationality).strip() or None)

    @app.template_filter("player_positions_display")
    def player_positions_display_filter(player: object) -> str:
        from app.services.player_ratings_csv import player_positions_display_label

        return player_positions_display_label(player)

    @app.template_filter("linkify_news_body")
    def linkify_news_body_filter(body: object):
        from app.league_db import db
        from app.services.news_entity_linkify import linkify_news_body
        from markupsafe import Markup

        if body is None or not str(body).strip():
            return Markup("")
        return linkify_news_body(db.session, str(body))

    from app.datetime_display import register_eastern_time_template_filter

    register_eastern_time_template_filter(app)

    @app.template_filter("team_stat_rate")
    def team_stat_rate_filter(value: object, gp: object, rate: str = "raw") -> float | int | None:
        from app.services.team_statistics import format_rate_value

        if value is None:
            return None
        gp_i = int(gp) if gp is not None else None
        try:
            num = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        out = format_rate_value("gf", num, gp=gp_i, rate=rate or "raw")
        if out is None:
            return None
        if rate == "raw":
            if abs(num - round(num)) < 1e-9:
                return int(round(num))
            return num
        return out

    @app.context_processor
    def inject_layout():
        from app.config import is_racing_league
        from app.services.draft_history import draft_pick_current_team_view
        from app.services.layout_nav_cache import get_nav_teams_for_layout

        slug_early = str(app.config.get("LEAGUE_SLUG") or "").strip()
        racing_layout = is_racing_league(slug_early)
        try:
            teams = get_nav_teams_for_layout(app) if not racing_layout else []
        except Exception:
            teams = []

        def team_logo_url(team: Team) -> str:
            return team_logo_url_for_team(team)

        from app.services.season_team_logo_bundle import get_season_team_logo_bundle

        try:
            _logo_bundle = get_season_team_logo_bundle(app)
            season_team_logo_url = _logo_bundle.season_team_logo_url
            team_logo_url_for_season_context = _logo_bundle.team_logo_url_for_season_context
            team_logo_url_present_franchise = _logo_bundle.team_logo_url_present_franchise
            season_team_name = _logo_bundle.season_team_name
            season_team_source_id = _logo_bundle.season_team_source_id
            draft_pick_team_logo_url = _logo_bundle.draft_pick_team_logo_url
        except Exception:
            def _empty_logo(*_a, **_k):
                return ""

            season_team_logo_url = _empty_logo
            team_logo_url_for_season_context = _empty_logo
            team_logo_url_present_franchise = _empty_logo
            season_team_name = lambda *_a, **_k: ""
            season_team_source_id = lambda *_a, **_k: None
            draft_pick_team_logo_url = _empty_logo

        def player_headshot_url(player: Player | None) -> str | None:
            from flask import url_for

            if not player:
                return None
            static_root = Path(app.root_path) / (app.static_folder or "static")
            rel = resolve_player_headshot_static_filename(
                static_root,
                player,
                app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
            )
            if not rel:
                return None
            return url_for("static", filename=rel)

        def history_team_award_era_logo_url(award: object) -> str | None:
            from flask import url_for

            from app.services.history_team_award_logos import history_team_award_era_logo_static_relpath

            rel = history_team_award_era_logo_static_relpath(award)
            if rel:
                return url_for("static", filename=rel)
            return None

        def history_team_award_notes_team_label(award: object) -> str | None:
            from app.services.history_team_award_logos import history_team_award_notes_team_label as _notes_label

            return _notes_label(award)

        def history_jim_gregory_era_logo_url(award: object) -> str | None:
            from flask import url_for

            from app.services.history_team_award_logos import history_jim_gregory_era_logo_static_relpath

            rel = history_jim_gregory_era_logo_static_relpath(award)
            if rel:
                return url_for("static", filename=rel)
            return None

        def league_logo_url() -> str:
            from app.logo_urls import league_logo_url as _league_logo_url

            return _league_logo_url()

        from flask import has_request_context, request
        from flask_login import current_user

        from app.auth_login import (
            ADMIN_ROLE_LEAGUE,
            ADMIN_ROLE_SUPER,
            active_membership_for_league,
            has_admin_role,
        )
        from app.services.gm_notifications import gm_inbox_badge_unread
        from app.services.site_announcements import active_announcement

        slug_layout = str(app.config.get("LEAGUE_SLUG") or "").strip()
        gm_membership = None
        gm_messages_unread = 0
        if getattr(current_user, "is_authenticated", False) and slug_layout:
            gm_membership = active_membership_for_league(current_user, slug_layout)
            if gm_membership or has_admin_role(current_user):
                try:
                    gm_messages_unread = gm_inbox_badge_unread(slug_layout, int(current_user.id))
                except Exception:
                    gm_messages_unread = 0
        ann = None
        if slug_layout:
            try:
                ann = active_announcement(db.session, slug_layout)
            except Exception:
                ann = None

        join_league_available_team_rows = []
        if slug_layout:
            try:
                from app.services.join_league import join_league_available_team_banner_rows

                join_league_available_team_rows = join_league_available_team_banner_rows(db.session)
            except Exception:
                join_league_available_team_rows = []

        admin_compact_layout = bool(
            has_request_context() and str(getattr(request, "path", "") or "").startswith("/admin")
        )

        header_team_logo_season = None
        if slug_layout in ("bowl-historical", "bowl-cap"):
            try:
                from app.services.seasons import get_current_season

                header_team_logo_season = get_current_season()
            except Exception:
                header_team_logo_season = None

        from app.services.relegation import relegation_under_construction

        return dict(
            nav_teams=teams,
            relegation_under_construction=relegation_under_construction(slug_layout),
            header_team_logo_season=header_team_logo_season,
            team_logo_url=team_logo_url,
            season_team_logo_url=season_team_logo_url,
            team_logo_url_for_season_context=team_logo_url_for_season_context,
            team_logo_url_present_franchise=team_logo_url_present_franchise,
            season_team_name=season_team_name,
            season_team_source_id=season_team_source_id,
            draft_pick_team_logo_url=draft_pick_team_logo_url,
            history_team_award_era_logo_url=history_team_award_era_logo_url,
            history_team_award_notes_team_label=history_team_award_notes_team_label,
            history_jim_gregory_era_logo_url=history_jim_gregory_era_logo_url,
            league_logo_url=league_logo_url,
            player_headshot_url=player_headshot_url,
            main_league_roster_team=main_league_roster_team,
            draft_pick_current_team_view=draft_pick_current_team_view,
            league_entries=LEAGUES,
            current_league_slug=app.config.get("LEAGUE_SLUG"),
            is_racing_league=racing_layout,
            gm_membership=gm_membership,
            gm_messages_unread=gm_messages_unread,
            active_site_announcement=ann,
            join_league_available_team_rows=join_league_available_team_rows,
            admin_compact_layout=admin_compact_layout,
            site_has_admin=has_admin_role(current_user)
            if getattr(current_user, "is_authenticated", False)
            else False,
            site_can_process_trades=has_admin_role(
                current_user, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER
            )
            if getattr(current_user, "is_authenticated", False)
            else False,
        )

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create database tables and FTS."""
        db.create_all()
        migrate_team_season_aggregates_sqlite(db.engine)
        repair_fhm_team_city_from_name(db.engine)
        ensure_players_jersey_number_sqlite(db.engine)
        ensure_player_overall_baseline_sqlite(db.engine)
        ensure_player_rating_snapshots_sqlite(db.engine)
        ensure_player_rating_snapshot_timeline_columns_sqlite(db.engine)
        ensure_player_analytics_snapshots_sqlite(db.engine)
        ensure_team_analytics_snapshots_sqlite(db.engine)
        ensure_advanced_stats_hub_snapshots_sqlite(db.engine)
        ensure_org_development_report_archives_sqlite(db.engine)
        ensure_team_season_aggregate_extra_columns(db.engine)
        ensure_skater_career_line_career_source_sqlite(db.engine)
        ensure_skater_career_line_extra_stats_sqlite(db.engine)
        ensure_skater_career_line_game_rating_sqlite(db.engine)
        ensure_player_goalie_stats_gsaa_sqlite(db.engine)
        ensure_advanced_stats_columns_sqlite(db.engine)
        ensure_game_record_baselines_sqlite(db.engine)
        ensure_record_leader_snapshots_sqlite(db.engine)
        ensure_record_stat_adjustments_sqlite(db.engine)
        ensure_history_awards_staff_fhm_id_sqlite(db.engine)
        ensure_history_records_admin_metadata_sqlite(db.engine)
        ensure_history_all_stars_sqlite(db.engine)
        ensure_fts5(db.engine)
        rebuild_player_fts(db.engine)
        print("Database initialized.")

    @app.cli.command("rebuild-fts")
    def rebuild_fts_cmd() -> None:
        rebuild_player_fts(db.engine)
        print("player_fts rebuilt.")

    @app.cli.command("set-admin")
    @click.argument("email")
    def set_admin_cmd(email: str) -> None:
        """Grant site admin to a user by email (site DB)."""
        from sqlalchemy import select

        from app.site_models import User

        u = db.session.scalar(select(User).where(User.email == email.strip().lower()).limit(1))
        if not u:
            print("User not found:", email)
            return
        u.is_admin = True
        u.admin_role = "super_admin"
        db.session.commit()
        print("Admin granted:", email)

    @app.cli.command("ap-credit-daily-export")
    def ap_credit_daily_export_cmd() -> None:
        """Credit +1 AP (UTC day, idempotent) for each team with an active GM if raw import dir was touched recently."""
        from pathlib import Path
        from time import time

        from sqlalchemy import select

        from app.models import Team
        from app.services.ap_service import maybe_credit_daily_export_for_team
        from app.site_models import GmLeagueMembership

        slug = str(app.config.get("LEAGUE_SLUG") or "")
        raw_dir = Path(app.config.get("RAW_IMPORT_DIR") or "")
        mtime = 0.0
        if raw_dir.is_dir():
            for p in raw_dir.rglob("*.csv"):
                try:
                    mtime = max(mtime, p.stat().st_mtime)
                except OSError:
                    continue
        if mtime < time() - 86400 * 3:
            print("No recent CSV activity in raw import dir (3d); skipping.")
            return
        teams = db.session.scalars(select(Team.id)).all()
        active_team_ids = set(
            db.session.scalars(
                select(GmLeagueMembership.team_id).where(
                    GmLeagueMembership.league_slug == slug,
                    GmLeagueMembership.status == "active",
                )
            ).all()
        )
        n = 0
        for tid in teams:
            if int(tid) not in active_team_ids:
                continue
            if maybe_credit_daily_export_for_team(slug, int(tid), raw_import_dir_mtime=mtime):
                n += 1
        print(f"ap-credit-daily-export ({slug}): credited {n} teams (max once each per UTC day).")

    @app.cli.command("backfill-plus-minus")
    def backfill_plus_minus_cmd() -> None:
        """Set player_skater_stats.plus_minus from player_skater_stats_*.csv (fixes pre-fix imports)."""
        from scripts.backfill_skater_plus_minus import backfill_skater_plus_minus

        n = backfill_skater_plus_minus()
        print(f"backfill_skater_plus_minus: applied {n} CSV rows")

    @app.cli.command("bowl-game-record-baselines-sync")
    def bowl_game_record_baselines_sync_cmd() -> None:
        """Persist game-log leaders into baseline rows for this league mount (survives season resets)."""
        from app.services.game_records import sync_game_record_baselines
        from app.sqlite_retry import commit_with_sqlite_retry

        slug = str(app.config.get("LEAGUE_SLUG") or "")
        promoted = sync_game_record_baselines(db.session)
        commit_with_sqlite_retry(db.session)
        print(f"bowl-game-record-baselines-sync ({slug}): promoted {promoted} baseline(s)")

    @app.cli.command("bowl-overall-baseline-refresh")
    def bowl_overall_baseline_refresh_cmd() -> None:
        """Save each player's current 1-100 OVR as the trend baseline (clears ↑/↓ until ratings move again).

        The import pipeline already snapshots OVR at the start of ``import_data.py`` / ``run_import``.
        Use this CLI only to reset baselines to the current computed OVR without running a full import.
        """
        from app.services.player_overall_score import refresh_all_player_overall_baselines

        n = refresh_all_player_overall_baselines(db.session)
        print(f"bowl-overall-baseline-refresh: stored baseline OVR for {n} players.")

    @app.cli.command("bowl-six-backfill-prizes")
    def bowl_six_backfill_prizes_cmd() -> None:
        """Finalize/repair weekly BOWL Six AP and credit ended seasons (current league mount)."""
        from app.services.bowl_six import (
            backfill_bowl_six_season_prizes,
            backfill_bowl_six_weekly_prizes,
        )

        slug = str(app.config.get("LEAGUE_SLUG") or "")
        weekly = backfill_bowl_six_weekly_prizes(db.session, db.session, slug)
        season = backfill_bowl_six_season_prizes(db.session, slug)
        db.session.commit()
        print(f"bowl-six-backfill-prizes ({slug}) weekly: {weekly}")
        print(f"bowl-six-backfill-prizes ({slug}) season: {season}")

    return app
