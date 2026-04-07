import colorsys
from pathlib import Path

from flask import Flask

from app.config import LEAGUES, Config
from app.db_utils import (
    ensure_fts5,
    ensure_players_jersey_number_sqlite,
    ensure_skater_career_line_career_source_sqlite,
    ensure_skater_career_line_extra_stats_sqlite,
    ensure_team_season_aggregate_extra_columns,
    migrate_team_season_aggregates_sqlite,
    rebuild_player_fts,
    repair_fhm_team_city_from_name,
)
from app.models import Player, db
from app.services.player_headshot import resolve_player_headshot_static_filename


def create_app(config_class: type = Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_relative_config=True,
    )
    app.config.from_object(config_class)

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

    with app.app_context():
        db.create_all()
        migrate_team_season_aggregates_sqlite(db.engine)
        repair_fhm_team_city_from_name(db.engine)
        ensure_players_jersey_number_sqlite(db.engine)
        ensure_team_season_aggregate_extra_columns(db.engine)
        ensure_skater_career_line_career_source_sqlite(db.engine)
        ensure_skater_career_line_extra_stats_sqlite(db.engine)
        ensure_fts5(db.engine)
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

    from sqlalchemy import select

    from app.logo_urls import team_logo_url_for_team
    from app.models import Player, Team
    from app.routes import api_bp, main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

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
        """Same gradient as ABI/POT but for raw attributes on a 0–21 scale (maps onto 0.5–5.0)."""
        if val is None:
            return ""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        v_norm = 0.5 + (v / 21.0) * (5.0 - 0.5)
        return rating_pill_style(v_norm)

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

    @app.context_processor
    def inject_layout():
        teams = db.session.scalars(select(Team).order_by(Team.name)).all()

        def team_logo_url(team: Team) -> str:
            return team_logo_url_for_team(team)

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

        return dict(
            nav_teams=teams,
            team_logo_url=team_logo_url,
            league_logo_url=league_logo_url,
            player_headshot_url=player_headshot_url,
            league_entries=LEAGUES,
            current_league_slug=app.config.get("LEAGUE_SLUG"),
        )

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create database tables and FTS."""
        db.create_all()
        migrate_team_season_aggregates_sqlite(db.engine)
        repair_fhm_team_city_from_name(db.engine)
        ensure_players_jersey_number_sqlite(db.engine)
        ensure_team_season_aggregate_extra_columns(db.engine)
        ensure_skater_career_line_career_source_sqlite(db.engine)
        ensure_skater_career_line_extra_stats_sqlite(db.engine)
        ensure_fts5(db.engine)
        rebuild_player_fts(db.engine)
        print("Database initialized.")

    @app.cli.command("rebuild-fts")
    def rebuild_fts_cmd() -> None:
        rebuild_player_fts(db.engine)
        print("player_fts rebuilt.")

    @app.cli.command("backfill-plus-minus")
    def backfill_plus_minus_cmd() -> None:
        """Set player_skater_stats.plus_minus from player_skater_stats_*.csv (fixes pre-fix imports)."""
        from scripts.backfill_skater_plus_minus import backfill_skater_plus_minus

        n = backfill_skater_plus_minus()
        print(f"backfill_skater_plus_minus: applied {n} CSV rows")

    return app
