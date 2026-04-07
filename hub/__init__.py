"""Root splash hub (multi-league entry). Mounted at ``/`` by ``wsgi.application``."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, render_template, send_from_directory

from app.config import BASE_DIR, LEAGUES


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

    return app
