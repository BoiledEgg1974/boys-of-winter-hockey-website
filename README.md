# Boys of Winter League (web)

Flask app: **hub** at `/` plus three league sites under `/bowl-historical`, `/bowl-fantasy`, and `/bowl-cap`.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5000`.

## Refreshing stats from FHM

See **[docs/DATA-UPDATE.md](docs/DATA-UPDATE.md)** (copy CSVs → `import_data.py` per league → restart the server).

For a **single ordered command** from saved-game exports through PythonAnywhere (STEP1 → STEP2, with STEP3 inside STEP1), use **`python scripts/run_site_update.py`** (default workflow `to-live`; see `python scripts/run_site_update.py --help`).

For a **numbered checklist** on a live nested deployment (proxy paths, venv, import order, reload), use **[docs/UPDATE-NESTED-SERVER.md](docs/UPDATE-NESTED-SERVER.md)**.

Script index and manual vs automated order: **[scripts/README.md](scripts/README.md)**.

## Production

WSGI entry: **`wsgi.application`**. Configure your host’s Python path and process manager to load this module from the project root.
