"""Root splash hub (multi-league entry). Mounted at ``/`` by ``wsgi.application``."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_from_directory

from app.config import BASE_DIR, LEAGUES, league_slugs


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


# Top-level paths served by each league app (no mount prefix). Bookmarks like /team/x 404 on the hub
# without these redirects.
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
    app = Flask(
        __name__,
        template_folder=str(hub_root / "templates"),
        static_folder=str(BASE_DIR / "app" / "static"),
        static_url_path="/static",
    )

    @app.get("/")
    def splash():
        audio_dir = Path(app.static_folder or "") / "audio"
        splash_mp3 = audio_dir / "splash.mp3"
        return render_template(
            "index.html",
            leagues=LEAGUES,
            splash_audio_available=splash_mp3.is_file(),
        )

    @app.get("/favicon.ico")
    def favicon():
        static = Path(app.static_folder or "")
        for name in ("favicon.ico", "favicon.svg"):
            p = static / name
            if p.is_file():
                return send_from_directory(static, name)
        return ("", 204)

    @app.get("/team/<path:rest>")
    def hub_redirect_team(rest: str):
        return _redirect_to_default_league(f"team/{rest}")

    @app.get("/player/<int:player_id>")
    def hub_redirect_player(player_id: int):
        return _redirect_to_default_league(f"player/{player_id}")

    @app.get("/game/<int:game_id>")
    def hub_redirect_game(game_id: int):
        return _redirect_to_default_league(f"game/{game_id}")

    @app.get("/api/<path:rest>")
    def hub_redirect_api(rest: str):
        return _redirect_to_default_league(f"api/{rest}")

    for _leaf in _HUB_LEAF_REDIRECTS:

        def _make_leaf_handler(segment: str):
            def _handler():
                return _redirect_to_default_league(segment)

            _handler.__name__ = f"hub_redirect_{segment.replace('-', '_')}"
            return _handler

        app.add_url_rule(f"/{_leaf}", f"hub_redirect_{_leaf.replace('-', '_')}", _make_leaf_handler(_leaf))

    return app
