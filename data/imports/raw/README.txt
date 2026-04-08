Raw FHM CSV exports (one folder per league on disk).

Folder names are league-specific (not the URL slug):

- ``data/imports/raw/bowl_fantasy`` for slug ``bowl-fantasy`` (BOWL-Fantasy)
- ``data/imports/raw/bowl_historical`` for slug ``bowl-historical`` (BOWL-Historical)
- ``data/imports/raw/bowl_cap`` for slug ``bowl-cap`` (BOWL-Cap)

Imports: set environment variable LEAGUE_SLUG to the slug (e.g. bowl-fantasy), and Config maps it to the
correct import folder above and to instance/<slug>.db.

If you still have instance/league.db from before multi-league support, copy it to instance/bowl-fantasy.db so the
Fantasy site keeps the same data.

After a slug rename, rename SQLite files to match: instance/bowl-historical.db, instance/bowl-fantasy.db,
instance/bowl-cap.db (from league2.db, bow.db, league3.db if applicable).

Flask CLI (init-db, rebuild-fts, etc.) targets one league per invocation — set LEAGUE_SLUG first, for example:

  set LEAGUE_SLUG=bowl-fantasy

Combined app (run.py / wsgi.py): each league is mounted under
``/bowl-historical``, ``/bowl-fantasy``, or ``/bowl-cap``; the proxy must pass those path prefixes through.
