import colorsys
import importlib
from pathlib import Path

import click
from flask import Flask, session
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from app.auth_login import login_manager
from app.config import LEAGUES, Config
from app.db_utils import (
    ensure_fts5,
    ensure_history_all_stars_sqlite,
    ensure_history_awards_staff_fhm_id_sqlite,
    ensure_players_jersey_number_sqlite,
    ensure_player_overall_baseline_sqlite,
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
    ensure_team_staff_roster_entries_sqlite,
    ensure_gm_trade_proposals_sqlite,
    ensure_story_publish_schedules_sqlite,
    ensure_story_publish_schedule_extra_columns_sqlite,
    ensure_awards_voting_sqlite,
    ensure_member_watchlists_sqlite,
    ensure_mobile_push_devices_sqlite,
    ensure_news_engagement_sqlite,
    ensure_admin_undo_actions_sqlite,
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
    ensure_team_season_aggregate_extra_columns,
    migrate_team_season_aggregates_sqlite,
    rebuild_player_fts,
    repair_fhm_team_city_from_name,
)
from app.models import Player, db
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
        binds["site"] = site_uri
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
    install_sqlite_connect_pragmas()
    csrf.init_app(app)
    login_manager.init_app(app)

    importlib.import_module("app.site_models")

    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if isinstance(db_uri, str) and db_uri.startswith("sqlite:///"):
        app.logger.info(
            "League %s using SQLite %s",
            app.config.get("LEAGUE_SLUG", "?"),
            db_uri.replace("sqlite:///", "", 1),
        )

    with app.app_context():
        db.create_all()
        migrate_team_season_aggregates_sqlite(db.engine)
        repair_fhm_team_city_from_name(db.engine)
        ensure_players_jersey_number_sqlite(db.engine)
        ensure_player_overall_baseline_sqlite(db.engine)
        ensure_team_season_aggregate_extra_columns(db.engine)
        ensure_skater_career_line_career_source_sqlite(db.engine)
        ensure_skater_career_line_extra_stats_sqlite(db.engine)
        ensure_skater_career_line_game_rating_sqlite(db.engine)
        ensure_player_goalie_stats_gsaa_sqlite(db.engine)
        ensure_history_awards_staff_fhm_id_sqlite(db.engine)
        ensure_history_all_stars_sqlite(db.engine)
        ensure_fts5(db.engine)
        try:
            site_engine = db.engines.get("site")
        except Exception:
            site_engine = None
        if site_engine is not None:
            ensure_homepage_module_settings_sqlite(site_engine)
            ensure_site_announcements_sqlite(site_engine)
            ensure_site_users_admin_role_sqlite(site_engine)
            ensure_password_reset_tokens_sqlite(site_engine)
            ensure_site_banned_identities_sqlite(site_engine)
            ensure_league_rule_settings_sqlite(site_engine)
            ensure_gm_approval_requests_sqlite(site_engine)
            ensure_staff_change_requests_sqlite(site_engine)
            ensure_team_staff_roster_entries_sqlite(site_engine)
            ensure_gm_trade_proposals_sqlite(site_engine)
            ensure_story_publish_schedules_sqlite(site_engine)
            ensure_story_publish_schedule_extra_columns_sqlite(site_engine)
            ensure_awards_voting_sqlite(site_engine)
            ensure_member_watchlists_sqlite(site_engine)
            ensure_mobile_push_devices_sqlite(site_engine)
            ensure_news_engagement_sqlite(site_engine)
            ensure_admin_undo_actions_sqlite(site_engine)
            ensure_bowl_six_slates_discord_columns_sqlite(site_engine)
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
            ensure_league_expansion_draft_columns_sqlite(site_engine)
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
            from app.services.ap_service import seed_ap_catalog_if_empty

            seed_ap_catalog_if_empty()
        except Exception as exc:
            app.logger.warning("AP catalog seed skipped: %s", exc)

        try:
            from app.services.bootstrap_site import ensure_commish_admin

            ensure_commish_admin(app)
        except Exception as exc:
            app.logger.warning("Commissioner bootstrap skipped: %s", exc)

    from sqlalchemy import select

    from app.logo_urls import team_logo_url_for_team
    from app.models import Player, Team
    from app.routes import api_bp, main_bp
    from app.routes.draft_hub import draft_hub_bp
    from app.routes.expansion_draft_hub import expansion_draft_hub_bp
    from app.routes.site_portal import site_admin_bp, site_gm_bp

    from app.routes import bowl_six_portal as _bowl_six_portal  # noqa: F401 — routes on shared blueprints

    app.register_blueprint(main_bp)
    app.register_blueprint(draft_hub_bp)
    app.register_blueprint(expansion_draft_hub_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    csrf.exempt(api_bp)
    app.register_blueprint(site_gm_bp)
    app.register_blueprint(site_admin_bp)

    if app.config.get("LEAGUE_JSON_CACHE_WARM_ON_STARTUP", True):
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

    @app.context_processor
    def inject_layout():
        from app.services.draft_history import draft_pick_current_team_view
        from app.services.layout_nav_cache import get_nav_teams_for_layout

        teams = get_nav_teams_for_layout(app)

        def team_logo_url(team: Team) -> str:
            return team_logo_url_for_team(team)

        from app.services.season_team_logo_bundle import get_season_team_logo_bundle

        _logo_bundle = get_season_team_logo_bundle(app)
        season_team_logo_url = _logo_bundle.season_team_logo_url
        team_logo_url_for_season_context = _logo_bundle.team_logo_url_for_season_context
        team_logo_url_present_franchise = _logo_bundle.team_logo_url_present_franchise
        season_team_name = _logo_bundle.season_team_name
        season_team_source_id = _logo_bundle.season_team_source_id
        draft_pick_team_logo_url = _logo_bundle.draft_pick_team_logo_url

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
            from flask import current_app, url_for

            static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
            slug = current_app.config.get("LEAGUE_SLUG")
            rel_dir = str(current_app.config.get("LEAGUE_LOGO_REL_DIR", "logos")).strip("/\\")
            rel_path = Path(rel_dir)
            specific_dir = static_root / rel_path
            if specific_dir.is_dir():
                for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                    if (specific_dir / name).is_file():
                        return url_for("static", filename=f"{rel_dir}/{name}")
            # Pre–slug-rename folders (logos/league2, logos/bow, logos/league3)
            _legacy_league_logo_dir = {
                "bowl-historical": "league2",
                "bowl-fantasy": "bow",
                "bowl-cap": "league3",
            }.get(slug or "")
            if _legacy_league_logo_dir:
                leg = static_root / "logos" / _legacy_league_logo_dir
                if leg.is_dir():
                    for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                        if (leg / name).is_file():
                            return url_for("static", filename=f"logos/{_legacy_league_logo_dir}/{name}")
            if slug:
                sub = static_root / "logos" / slug
                if sub.is_dir():
                    for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                        if (sub / name).is_file():
                            return url_for("static", filename=f"logos/{slug}/{name}")
            for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                if (static_root / "logos" / name).is_file():
                    return url_for("static", filename=f"logos/{name}")
            return url_for("static", filename="logos/league-placeholder.svg")

        from flask import has_request_context, request
        from flask_login import current_user

        from app.auth_login import active_membership_for_league, has_admin_role
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

        return dict(
            nav_teams=teams,
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
            gm_membership=gm_membership,
            gm_messages_unread=gm_messages_unread,
            active_site_announcement=ann,
            admin_compact_layout=admin_compact_layout,
            site_has_admin=has_admin_role(current_user)
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
        ensure_team_season_aggregate_extra_columns(db.engine)
        ensure_skater_career_line_career_source_sqlite(db.engine)
        ensure_skater_career_line_extra_stats_sqlite(db.engine)
        ensure_skater_career_line_game_rating_sqlite(db.engine)
        ensure_player_goalie_stats_gsaa_sqlite(db.engine)
        ensure_history_awards_staff_fhm_id_sqlite(db.engine)
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

    @app.cli.command("bowl-overall-baseline-refresh")
    def bowl_overall_baseline_refresh_cmd() -> None:
        """Save each player's current 1-100 OVR as the trend baseline (clears ↑/↓ until ratings move again).

        The import pipeline already snapshots OVR at the start of ``import_data.py`` / ``run_import``.
        Use this CLI only to reset baselines to the current computed OVR without running a full import.
        """
        from app.services.player_overall_score import refresh_all_player_overall_baselines

        n = refresh_all_player_overall_baselines(db.session)
        print(f"bowl-overall-baseline-refresh: stored baseline OVR for {n} players.")

    return app
