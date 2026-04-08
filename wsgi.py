"""
Combined WSGI entry: splash hub at ``/`` and one Flask league app per slug under ``/<slug>/``.

Deployment: proxy the full request path to this app (do not strip ``/bowl-fantasy`` etc.).

Per-league imports: set ``LEAGUE_SLUG`` (e.g. ``bowl-fantasy``) and run the import pipeline; CSVs live in
``data/imports/raw/<folder>/`` per league registry, databases in ``instance/<slug>.db``. Override DB with ``DATABASE_URL`` only when
using the default single ``create_app(Config)`` path—not when mounting via this module.

Local dev: ``python run.py`` serves this application.
"""
from __future__ import annotations

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from app import create_app
from app.config import league_slugs, make_league_config
from hub import create_hub_app


def create_combined_application():
    hub = create_hub_app()
    mounts = {f"/{slug}": create_app(make_league_config(slug)).wsgi_app for slug in league_slugs()}
    return DispatcherMiddleware(hub.wsgi_app, mounts)


application = create_combined_application()
