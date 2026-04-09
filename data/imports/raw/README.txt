Raw FHM CSV exports (one folder per league on disk).

Folder names are league-specific (not the URL slug):

- ``data/imports/raw/bowl_fantasy`` for slug ``bowl-fantasy`` (BOWL-Fantasy)
- ``data/imports/raw/bowl_historical`` for slug ``bowl-historical`` (BOWL-Historical)
- ``data/imports/raw/bowl_cap`` for slug ``bowl-cap`` (BOWL-Cap)

Imports: set environment variable LEAGUE_SLUG to the slug (e.g. bowl-fantasy), and Config maps it to the
correct import folder above and to instance/<slug>.db.

The app picks the SQLite file per league by **content**, not only by filename: it prefers
``instance/<slug>.db`` if that file has imported rows (teams/players/games/seasons). If the new file is empty
but a legacy ``league2.db`` / ``bow.db`` / ``league3.db`` still exists, it uses the legacy file
(so an empty DB created after a slug rename does not shadow your real database).

**Blank home / “No standings data yet”:** the API needs at least one **season** and related rows. If the
server’s ``instance/`` folder never received a populated DB, or CSVs were never imported for that league,
you must **re-import**. From the repo root (once per league):

  set LEAGUE_SLUG=bowl-historical
  python scripts/import_data.py

Repeat with ``bowl-fantasy`` and ``bowl-cap``. On PythonAnywhere, run the same in a Bash console with the
venv activated, or from your PC run ``Deploy-To-PythonAnywhere.bat`` / ``python scripts/pythonanywhere.py deploy``
(pip install -r requirements-deploy.txt first). The deploy command will ask where each league's CSV folder
lives on your machine (saved under ``scripts/pythonanywhere_csv_sources.json``, gitignored) unless you pass
``--repo-csv`` to use only the folders under ``data/imports/raw/`` in this repo.

After restart, check the app log for a line like ``League bowl-historical using SQLite …`` to confirm which
file path each site uses.

If you set ``DATABASE_URL`` explicitly, it must point at an existing file (update the path after a slug
rename, or remove it to use automatic resolution).

If you still have instance/league.db from before multi-league support, copy it to instance/bow.db or
instance/bowl-fantasy.db so the Fantasy site keeps the same data.

Flask CLI (init-db, rebuild-fts, etc.) targets one league per invocation — set LEAGUE_SLUG first, for example:

  set LEAGUE_SLUG=bowl-fantasy

Combined app (run.py / wsgi.py): each league is mounted under
``/bowl-historical``, ``/bowl-fantasy``, or ``/bowl-cap``; the proxy must pass those path prefixes through.
