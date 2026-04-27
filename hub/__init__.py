"""Root splash hub (multi-league entry). Mounted at ``/`` by ``wsgi.application``."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_from_directory, session
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from app.auth_login import create_login_manager
from app.config import BASE_DIR, Config, LEAGUES, league_slugs, resolve_site_sqlite_path
from app.league_db import db

csrf = CSRFProtect()
login_manager = create_login_manager()


def _default_league_slug() -> str:
    return os.environ.get("DEFAULT_LEAGUE_SLUG", league_slugs()[0])


def _redirect_to_default_league(rel_path: str):
    """Send legacy unprefixed URLs to the first league mount (DispatcherMiddleware)."""
    slug = _default_league_slug()
    q = request.query_string.decode()
    loc = f"/{slug}/{rel_path.lstrip('/')}"
    if q:
        loc = f"{loc}?{q}"
    return redirect(loc, code=302)


_HUB_LEAF_REDIRECTS: tuple[str, ...] = (
    "standings",
    "statistics",
    "schedule",
    "history",
    "records",
    "prospects",
    "undrafted-prospects",
    "free-agents",
    "draft",
    "search",
)


def create_hub_app() -> Flask:
    hub_root = Path(__file__).resolve().parent
    hub_app = Flask(
        __name__,
        template_folder=str(hub_root / "templates"),
        static_folder=str(BASE_DIR / "app" / "static"),
        static_url_path="/static",
    )
    site_uri = str(hub_app.config.get("SITE_SQLALCHEMY_DATABASE_URI", "")).strip() or os.environ.get(
        "SITE_DATABASE_URL", f"sqlite:///{resolve_site_sqlite_path()}"
    )
    hub_app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", Config.SECRET_KEY),
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"site": site_uri},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SITE_SQLALCHEMY_DATABASE_URI=site_uri,
        JOIN_LEAGUE_RECIPIENT=Config.JOIN_LEAGUE_RECIPIENT,
        ADMIN_ALERT_EMAILS=Config.ADMIN_ALERT_EMAILS,
        MAIL_SMTP_HOST=Config.MAIL_SMTP_HOST,
        MAIL_SMTP_PORT=Config.MAIL_SMTP_PORT,
        MAIL_SMTP_USERNAME=Config.MAIL_SMTP_USERNAME,
        MAIL_SMTP_PASSWORD=Config.MAIL_SMTP_PASSWORD,
        MAIL_FROM=Config.MAIL_FROM,
        MAIL_SMTP_USE_TLS=Config.MAIL_SMTP_USE_TLS,
        MAIL_SMTP_USE_SSL=Config.MAIL_SMTP_USE_SSL,
        WTF_CSRF_TIME_LIMIT=None,
        SESSION_IDLE_TIMEOUT_MINUTES=Config.SESSION_IDLE_TIMEOUT_MINUTES,
        PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME,
        COMMISH_ADMIN_PASSWORD=Config.COMMISH_ADMIN_PASSWORD,
    )

    @hub_app.before_request
    def _idle_timeout_touch_session():
        # Sliding idle timeout for authenticated users (default 30 minutes).
        if getattr(current_user, "is_authenticated", False):
            session.permanent = True
            session.modified = True

    db.init_app(hub_app)
    csrf.init_app(hub_app)
    login_manager.init_app(hub_app)

    importlib.import_module("app.site_models")

    with hub_app.app_context():
        db.create_all()
        from app.services.ap_service import seed_ap_catalog_if_empty

        seed_ap_catalog_if_empty()

        try:
            from app.services.bootstrap_site import ensure_commish_admin

            ensure_commish_admin(hub_app)
        except Exception:
            pass

    from app.routes.hub_auth import hub_auth_bp

    hub_app.register_blueprint(hub_auth_bp)

    @hub_app.get("/")
    def splash():
        audio_dir = Path(hub_app.static_folder or "") / "audio"
        splash_mp3 = audio_dir / "splash.mp3"
        return render_template(
            "index.html",
            leagues=LEAGUES,
            splash_audio_available=splash_mp3.is_file(),
        )

    @hub_app.get("/favicon.ico")
    def favicon():
        static = Path(hub_app.static_folder or "")
        for name in ("favicon.ico", "favicon.svg"):
            p = static / name
            if p.is_file():
                return send_from_directory(static, name)
        return ("", 204)

    @hub_app.get("/team/<path:rest>")
    def hub_redirect_team(rest: str):
        return _redirect_to_default_league(f"team/{rest}")

    @hub_app.get("/player/<int:player_id>")
    def hub_redirect_player(player_id: int):
        return _redirect_to_default_league(f"player/{player_id}")

    @hub_app.get("/game/<int:game_id>")
    def hub_redirect_game(game_id: int):
        return _redirect_to_default_league(f"game/{game_id}")

    @hub_app.get("/api/<path:rest>")
    def hub_redirect_api(rest: str):
        return _redirect_to_default_league(f"api/{rest}")

    for _leaf in _HUB_LEAF_REDIRECTS:

        def _make_leaf_handler(segment: str):
            def _handler():
                return _redirect_to_default_league(segment)

            _handler.__name__ = f"hub_redirect_{segment.replace('-', '_')}"
            return _handler

        hub_app.add_url_rule(f"/{_leaf}", f"hub_redirect_{_leaf.replace('-', '_')}", _make_leaf_handler(_leaf))

    return hub_app
