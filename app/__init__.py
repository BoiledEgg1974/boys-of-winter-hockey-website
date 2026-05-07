import colorsys
import importlib
import re
from pathlib import Path

import click
from flask import Flask, session
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from app.auth_login import create_login_manager
from app.config import LEAGUES, Config, league_slugs
from app.db_utils import (
    ensure_fts5,
    ensure_history_awards_staff_fhm_id_sqlite,
    ensure_players_jersey_number_sqlite,
    ensure_player_overall_baseline_sqlite,
    ensure_homepage_module_settings_sqlite,
    ensure_site_announcements_sqlite,
    ensure_site_users_admin_role_sqlite,
    ensure_league_rule_settings_sqlite,
    ensure_gm_approval_requests_sqlite,
    ensure_gm_trade_proposals_sqlite,
    ensure_story_publish_schedules_sqlite,
    ensure_story_publish_schedule_extra_columns_sqlite,
    ensure_awards_voting_sqlite,
    ensure_member_watchlists_sqlite,
    ensure_news_engagement_sqlite,
    ensure_admin_undo_actions_sqlite,
    ensure_discord_outbound_sqlite,
    ensure_prospect_system_rank_snapshots_sqlite,
    ensure_positional_rank_snapshots_sqlite,
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

csrf = CSRFProtect()
login_manager = create_login_manager()
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
        ensure_fts5(db.engine)
        try:
            site_engine = db.engines.get("site")
        except Exception:
            site_engine = None
        if site_engine is not None:
            ensure_homepage_module_settings_sqlite(site_engine)
            ensure_site_announcements_sqlite(site_engine)
            ensure_site_users_admin_role_sqlite(site_engine)
            ensure_league_rule_settings_sqlite(site_engine)
            ensure_gm_approval_requests_sqlite(site_engine)
            ensure_gm_trade_proposals_sqlite(site_engine)
            ensure_story_publish_schedules_sqlite(site_engine)
            ensure_story_publish_schedule_extra_columns_sqlite(site_engine)
            ensure_awards_voting_sqlite(site_engine)
            ensure_member_watchlists_sqlite(site_engine)
            ensure_news_engagement_sqlite(site_engine)
            ensure_admin_undo_actions_sqlite(site_engine)
            ensure_discord_outbound_sqlite(site_engine)
            ensure_prospect_system_rank_snapshots_sqlite(site_engine)
            ensure_positional_rank_snapshots_sqlite(site_engine)
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
    from app.routes.site_portal import site_admin_bp, site_gm_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(draft_hub_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    csrf.exempt(api_bp)
    app.register_blueprint(site_gm_bp)
    app.register_blueprint(site_admin_bp)

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

    @app.context_processor
    def inject_layout():
        from app.services.draft_history import draft_pick_current_team_view

        teams = db.session.scalars(select(Team).order_by(Team.name)).all()

        def team_logo_url(team: Team) -> str:
            return team_logo_url_for_team(team)

        historical_team_logo_rel_by_id: dict[str, str] = {}
        historical_team_logo_rel_by_name: dict[str, str] = {}
        historical_team_name_by_id: dict[str, str] = {}
        historical_team_name_rows_by_id: dict[str, list[tuple[int, str]]] = {}
        historical_team_name_override_by_id_year: dict[tuple[str, int], str] = {}
        historical_team_logo_override_by_id_year: dict[tuple[str, int], str] = {}
        historical_team_logo_timeline_by_name_year: dict[tuple[str, int], str] = {}
        def _norm_team_logo_name(s: str) -> str:
            return " ".join(
                str(s or "")
                .lower()
                .replace("-", " ")
                .replace("_", " ")
                .split()
            )
        def _record_start_year(record: object) -> int | None:
            for attr in ("season_year", "start_year"):
                v = getattr(record, attr, None)
                try:
                    if v is not None:
                        return int(v)
                except Exception:
                    pass
            label = getattr(record, "season_year_label", None)
            if label and "-" in str(label):
                try:
                    return int(str(label).split("-", 1)[0])
                except Exception:
                    return None
            return None
        def _historical_name_for_tid(record: object, tid_s: str) -> str | None:
            sy = _record_start_year(record)
            if sy is not None:
                ovr = historical_team_name_override_by_id_year.get((tid_s, sy))
                if ovr:
                    return ovr
            rows = historical_team_name_rows_by_id.get(tid_s) or []
            if sy is not None:
                for row_year, row_name in rows:
                    if row_year == sy:
                        return row_name
            if tid_s in historical_team_name_by_id:
                return historical_team_name_by_id[tid_s]
            if rows:
                return rows[0][1]
            return None
        def _record_name_candidates(record: object) -> list[str]:
            out: list[str] = []
            for attr in ("team_name_override", "team_name"):
                v = getattr(record, attr, None)
                if v:
                    out.append(_norm_team_logo_name(str(v)))
            if hasattr(record, "record"):
                rec = getattr(record, "record")
                if rec is not None:
                    for attr in ("team_name_override", "team_name"):
                        v = getattr(rec, attr, None)
                        if v:
                            out.append(_norm_team_logo_name(str(v)))
            team_obj = getattr(record, "team", None)
            if team_obj is not None:
                for attr in ("full_display_name", "name", "city", "nickname"):
                    v = getattr(team_obj, attr, None)
                    if callable(v):
                        try:
                            v = v()
                        except Exception:
                            v = None
                    if v:
                        out.append(_norm_team_logo_name(str(v)))
                city = getattr(team_obj, "city", None)
                nick = getattr(team_obj, "nickname", None)
                if city and nick:
                    out.append(_norm_team_logo_name(f"{city} {nick}"))
            # preserve order, remove duplicates
            dedup: list[str] = []
            seen: set[str] = set()
            for nm in out:
                if nm and nm not in seen:
                    seen.add(nm)
                    dedup.append(nm)
            return dedup
        if str(app.config.get("LEAGUE_SLUG") or "") in league_slugs():
            team_logos_rel = str(app.config.get("TEAM_LOGOS_REL_DIR") or "logos/teams").replace("\\", "/").strip("/")
            team_logos_dir = Path(str(app.config.get("TEAM_LOGOS_DIR") or ""))
            static_root = Path(app.root_path) / "static"
            logo_scan_dirs: list[Path] = []
            if team_logos_dir.is_dir():
                logo_scan_dirs.append(team_logos_dir)
            # Cap uses logos/teams/bowl_cap for defaults; era/timeline art lives under bowl_historical (same as Historical app).
            if str(app.config.get("LEAGUE_SLUG") or "") == "bowl-cap":
                shared_hist = static_root / "logos" / "teams" / "bowl_historical"
                if shared_hist.is_dir() and shared_hist.resolve() != team_logos_dir.resolve():
                    logo_scan_dirs.append(shared_hist)
            for scan_dir in logo_scan_dirs:
                for p in scan_dir.iterdir():
                    if not p.is_file() or p.suffix.lower() not in (".png", ".webp", ".jpg", ".jpeg", ".svg"):
                        continue
                    try:
                        rel = p.relative_to(static_root)
                    except ValueError:
                        continue
                    rel_s = str(rel).replace("\\", "/")
                    m = re.search(r"-t(\d+)$", p.stem.lower())
                    if m:
                        tid = m.group(1)
                        historical_team_logo_rel_by_id[tid] = rel_s
                    # Optional explicit-name fallback: filenames like "*-montreal-wanderers.*"
                    parts = p.stem.lower().split("-", 1)
                    if len(parts) == 2 and parts[1].strip():
                        historical_team_logo_rel_by_name[_norm_team_logo_name(parts[1])] = rel_s
                    # Timeline: "<team_name>_<start>-<end>.png" or "<team_name>_<start>-present.png"
                    # ("present" = still in use; mapped through season year 2100, same as team_identity_history.csv).
                    tm = re.search(r"^(.+?)_(\d{4})-(present|\d{4})$", p.stem.lower())
                    if tm:
                        key = _norm_team_logo_name(tm.group(1))
                        try:
                            yr0 = int(tm.group(2))
                        except Exception:
                            yr0 = -1
                        end_tok = tm.group(3)
                        if end_tok == "present":
                            yr1 = 2100
                        else:
                            try:
                                yr1 = int(end_tok)
                            except Exception:
                                yr1 = -1
                        if yr0 > 0 and yr1 > 0:
                            for yy in range(min(yr0, yr1), max(yr0, yr1) + 1):
                                historical_team_logo_timeline_by_name_year[(key, yy)] = rel_s
                    # Single season start year: "<team_name>_<YYYY>.png" (one year only).
                    sm = re.search(r"^(.+?)_(\d{4})$", p.stem.lower())
                    if sm:
                        key = _norm_team_logo_name(sm.group(1))
                        try:
                            y1 = int(sm.group(2))
                        except Exception:
                            y1 = -1
                        if y1 > 0:
                            historical_team_logo_timeline_by_name_year[(key, y1)] = rel_s
            # Read per-team fallback names from league team season template (Team Name Override).
            raw_dir = Path(str(app.config.get("RAW_IMPORT_DIR") or ""))
            tsr = raw_dir / "team_season_records_template.csv"
            if tsr.is_file():
                try:
                    import csv

                    with tsr.open("r", encoding="utf-8-sig", newline="") as f:
                        sample = f.read(2048)
                        f.seek(0)
                        delim = ";" if sample.count(";") >= sample.count(",") else ","
                        rdr = csv.DictReader(f, delimiter=delim)
                        for row in rdr:
                            tid = (row.get("Team ID") or row.get("team_id") or "").strip()
                            nm = (row.get("Team Name Override") or row.get("team_name_override") or "").strip()
                            year = (row.get("Year") or row.get("season") or "").strip()
                            try:
                                start_year = int(str(year).split("-", 1)[0]) if year and "-" in year else int(year)
                            except Exception:
                                start_year = None
                            if tid and nm and tid not in historical_team_name_by_id:
                                historical_team_name_by_id[tid] = nm
                            if tid and nm and start_year is not None:
                                historical_team_name_rows_by_id.setdefault(tid, []).append((start_year, nm))
                except Exception:
                    pass
            # Optional per-year identity overrides:
            # team_fhm_id,start_year,end_year,team_name,logo_file
            ident_csv = raw_dir / "team_identity_history.csv"
            if ident_csv.is_file():
                try:
                    import csv

                    with ident_csv.open("r", encoding="utf-8-sig", newline="") as f:
                        sample = f.read(2048)
                        f.seek(0)
                        delim = ";" if sample.count(";") >= sample.count(",") else ","
                        rdr = csv.DictReader(f, delimiter=delim)
                        for row in rdr:
                            tid = str(
                                (row.get("team_fhm_id") or row.get("team_id") or "").strip()
                            )
                            name = str(
                                (row.get("team_name") or row.get("display_name") or "").strip()
                            )
                            logo = str(
                                (row.get("logo_file") or row.get("logo_file_override") or "").strip()
                            )
                            try:
                                y0 = int(
                                    str(
                                        row.get("start_year")
                                        or row.get("year_start")
                                        or row.get("year")
                                        or ""
                                    ).strip()
                                )
                            except Exception:
                                continue
                            try:
                                y1 = int(str(row.get("end_year") or row.get("year_end") or y0).strip())
                            except Exception:
                                y1 = y0
                            if logo and not logo.startswith("logos/"):
                                logo = f"{team_logos_rel}/{logo}"
                            for yy in range(min(y0, y1), max(y0, y1) + 1):
                                if tid:
                                    if name:
                                        historical_team_name_override_by_id_year[(tid, yy)] = name
                                    if logo:
                                        historical_team_logo_override_by_id_year[(tid, yy)] = logo
                                if name and logo:
                                    historical_team_logo_timeline_by_name_year[
                                        (_norm_team_logo_name(name), yy)
                                    ] = logo
                except Exception:
                    pass
            # NHL-era defaults (historical + cap); fantasy uses team_identity_history.csv only.
            if str(app.config.get("LEAGUE_SLUG") or "") in ("bowl-historical", "bowl-cap"):
                hist_logo_root = "logos/teams/bowl_historical"
                historical_team_logo_rel_by_name.setdefault(
                    "ottawa senators", f"{hist_logo_root}/ott-ottawa-senators.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "montreal wanderers", f"{hist_logo_root}/mtw-montreal-wanderers.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "montreal maroons", f"{hist_logo_root}/montreal_maroons_1924.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "pittsburgh pirates", f"{hist_logo_root}/pit-t7.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "philadelphia quakers", f"{hist_logo_root}/philadelphia_quakers.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "st louis eagles", f"{hist_logo_root}/st__louis_eagles.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "quebec bulldogs", f"{hist_logo_root}/quebec_bulldogs.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "hamilton tigers", f"{hist_logo_root}/hamilton_tigers.png"
                )
                historical_team_logo_rel_by_name.setdefault(
                    "new york americans", f"{hist_logo_root}/new_york_americans.png"
                )
                # Historical correction for reused Team ID 4 on early Joe Malone rows.
                historical_team_name_override_by_id_year[("4", 1919)] = "Quebec Bulldogs"
                historical_team_logo_override_by_id_year[("4", 1919)] = f"{hist_logo_root}/quebec_bulldogs.png"
                historical_team_name_override_by_id_year[("4", 1920)] = "Hamilton Tigers"
                historical_team_logo_override_by_id_year[("4", 1920)] = f"{hist_logo_root}/hamilton_tigers.png"
                historical_team_name_override_by_id_year[("4", 1921)] = "Hamilton Tigers"
                historical_team_logo_override_by_id_year[("4", 1921)] = f"{hist_logo_root}/hamilton_tigers.png"
                historical_team_name_override_by_id_year[("4", 1922)] = "Hamilton Tigers"
                historical_team_logo_override_by_id_year[("4", 1922)] = f"{hist_logo_root}/hamilton_tigers.png"
                historical_team_name_override_by_id_year[("4", 1923)] = "Hamilton Tigers"
                historical_team_logo_override_by_id_year[("4", 1923)] = f"{hist_logo_root}/hamilton_tigers.png"
                historical_team_name_override_by_id_year[("4", 1924)] = "Hamilton Tigers"
                historical_team_logo_override_by_id_year[("4", 1924)] = f"{hist_logo_root}/hamilton_tigers.png"
                historical_team_name_override_by_id_year[("4", 1925)] = "New York Americans"
                historical_team_logo_override_by_id_year[("4", 1925)] = f"{hist_logo_root}/new_york_americans.png"
                historical_team_name_override_by_id_year[("4", 1926)] = "New York Americans"
                historical_team_logo_override_by_id_year[("4", 1926)] = f"{hist_logo_root}/new_york_americans.png"

        def season_team_logo_url(record: object) -> str | None:
            from collections.abc import Mapping

            from flask import url_for

            if isinstance(record, Mapping):
                inner = record.get("record")
                if inner is not None:
                    record = inner

            # Explicit season-row override from CSV always wins.
            logo_override_rel = getattr(record, "logo_file_override", None) or getattr(
                record, "team_logo_override_rel", None
            )
            if logo_override_rel:
                rel = str(logo_override_rel).lstrip("/\\").replace("\\", "/")
                if rel.startswith("static/"):
                    rel = rel[7:]
                if rel:
                    return url_for("static", filename=rel)

            # Historical site: map Team ID -> logos/teams/bowl_historical/*-t<ID>.*
            tid = getattr(record, "team_fhm_id_csv", None)
            if tid is None and hasattr(record, "record"):
                tid = getattr(getattr(record, "record"), "team_fhm_id_csv", None)
            if tid is None:
                tid = getattr(record, "team_fhm_id", None)
            if tid is None:
                team_obj = getattr(record, "team", None)
                if team_obj is not None:
                    tid = getattr(team_obj, "fhm_team_id", None)
            tid_s = str(tid or "").strip()
            sy = _record_start_year(record)
            if tid_s and sy is not None:
                rel = historical_team_logo_override_by_id_year.get((tid_s, sy))
                if rel:
                    return url_for("static", filename=rel)
            # Timeline naming convention by team name and year wins over generic Team-ID logos.
            if sy is not None:
                for nm in _record_name_candidates(record):
                    rel = historical_team_logo_timeline_by_name_year.get((nm, sy))
                    if rel:
                        return url_for("static", filename=rel)
            if tid_s and tid_s in historical_team_logo_rel_by_id:
                return url_for("static", filename=historical_team_logo_rel_by_id[tid_s])
            # If a historical Team ID has no `*-t<ID>` asset, fall back via known team name.
            name_from_tid = _historical_name_for_tid(record, tid_s) if tid_s else None
            if name_from_tid:
                nm = _norm_team_logo_name(name_from_tid)
                rel = historical_team_logo_rel_by_name.get(nm)
                if rel:
                    return url_for("static", filename=rel)

            # Name-only fallback (historical rows without team id), e.g. "Montreal Wanderers".
            for nm in _record_name_candidates(record):
                rel = historical_team_logo_rel_by_name.get(nm)
                if rel:
                    return url_for("static", filename=rel)

            team_obj = getattr(record, "team", None)
            if team_obj:
                return team_logo_url_for_team(team_obj)
            return None

        def season_team_name(record: object) -> str | None:
            # Per-row CSV override wins. When it is blank and the row resolves to a Team,
            # use that franchise's name — do not let another template row with the same
            # Team ID (e.g. Montreal Maroons vs Canadiens both as FHM id 0) steal the label.
            ovr = getattr(record, "team_name_override", None)
            if ovr and str(ovr).strip():
                return str(ovr).strip()
            tid = getattr(record, "team_fhm_id_csv", None)
            if tid is None:
                tid = getattr(record, "team_fhm_id", None)
            team_obj = getattr(record, "team", None)
            if tid is None and team_obj is not None:
                tid = getattr(team_obj, "fhm_team_id", None)
            tid_s = str(tid or "").strip()
            sy = _record_start_year(record)
            if tid_s and sy is not None:
                id_ovr = historical_team_name_override_by_id_year.get((tid_s, sy))
                if id_ovr:
                    return id_ovr
            if team_obj is not None:
                return team_obj.full_display_name()
            if tid_s:
                rows = historical_team_name_rows_by_id.get(tid_s) or []
                if sy is not None:
                    for row_year, row_name in rows:
                        if row_year == sy:
                            return row_name
                if tid_s in historical_team_name_by_id:
                    return historical_team_name_by_id[tid_s]
                if rows:
                    return rows[0][1]
            return None
        def season_team_source_id(record: object) -> str | None:
            tid = getattr(record, "team_fhm_id_csv", None)
            if tid is None:
                tid = getattr(record, "team_fhm_id", None)
            tid_s = str(tid or "").strip()
            return tid_s or None

        def team_logo_url_for_season_context(
            team: Team | None, season: object | int | None
        ) -> str:
            """Era-accurate logo for Historical/Cap/Fantasy when season start year is known; else default team asset.

            *season* may be a :class:`~app.models.Season`, any object with ``start_year``, or an ``int`` year.
            """
            from types import SimpleNamespace

            from flask import url_for

            if team is None:
                return url_for("static", filename="logos/teams/placeholder.svg")
            slug = str(app.config.get("LEAGUE_SLUG") or "")
            sy: int | None
            if isinstance(season, int):
                sy = int(season)
            elif season is not None:
                sy = getattr(season, "start_year", None)
                if sy is not None:
                    sy = int(sy)
            else:
                sy = None
            if sy is not None and slug in ("bowl-historical", "bowl-cap", "bowl-fantasy"):
                tid = getattr(team, "fhm_team_id", None)
                tid_s = str(tid).strip() if tid is not None and str(tid).strip() else None
                proxy = SimpleNamespace(
                    team=team,
                    start_year=int(sy),
                    season_year=int(sy),
                    team_fhm_id_csv=tid_s,
                )
                era = season_team_logo_url(proxy)
                if era:
                    return era
            return team_logo_url(team)

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

        from flask_login import current_user

        from app.auth_login import active_membership_for_league
        from app.services.gm_notifications import gm_inbox_badge_unread
        from app.services.site_announcements import active_announcement

        slug_layout = str(app.config.get("LEAGUE_SLUG") or "").strip()
        gm_membership = None
        gm_messages_unread = 0
        if getattr(current_user, "is_authenticated", False) and slug_layout:
            gm_membership = active_membership_for_league(current_user, slug_layout)
            if gm_membership or getattr(current_user, "is_admin", False):
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
            season_team_name=season_team_name,
            season_team_source_id=season_team_source_id,
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
        """Save each player's current 1-100 OVR as the comparison baseline for trend arrows (per league DB)."""
        from app.services.player_overall_score import refresh_all_player_overall_baselines

        n = refresh_all_player_overall_baselines(db.session)
        print(f"bowl-overall-baseline-refresh: stored baseline OVR for {n} players.")

    return app
