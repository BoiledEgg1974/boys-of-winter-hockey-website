Multi-league CSV layout
========================

Each league has its own subdirectory under this folder (see LEAGUES in app/config.py).
Folder names are league-specific (not the URL slug):

- ``data/imports/raw/bowl_fantasy`` for slug ``bow`` (BOWL Fantasy)
- ``data/imports/raw/bowl_historical`` for slug ``league2`` (BOWL Historical)
- ``data/imports/raw/bowl_cap`` for slug ``league3`` (BOWL Cap)

Imports: set environment variable LEAGUE_SLUG to the slug (e.g. bow), and Config maps it to the
correct import folder above and to instance/<slug>.db.

If you still have instance/league.db from before multi-league support, copy it to instance/bow.db so the
``bow`` site keeps the same data.

Flask CLI (init-db, rebuild-fts, etc.) targets one league per invocation — set LEAGUE_SLUG first, for example:

  set LEAGUE_SLUG=bow
  set FLASK_APP=app:create_app
  flask init-db

Reverse proxies (nginx, Caddy, etc.): forward the full URI path to the WSGI app. Do not strip the
``/bow``, ``/league2``, or ``/league3`` prefix; the combined application in wsgi.py expects those segments.

Post-import safeguard
---------------------

Running ``python scripts/import_data.py`` now automatically executes the regression check
``tests.test_depth_chart_org_guard`` after data refresh. This protects all three sites from
cross-team depth chart leaks caused by mismatched line assignments.
