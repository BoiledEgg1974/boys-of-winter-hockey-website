# BOWL Perfect Squad — site mount

Perfect Squad is nested under each **hockey** league app:

| League | URL |
|--------|-----|
| Historical | `/bowl-historical/perfect-squad/bowl-historical/` |
| Cap | `/bowl-cap/perfect-squad/bowl-cap/` |
| Relegation | `/bowl-fantasy/perfect-squad/bowl-fantasy/` |

Implementation: `wsgi.py` wraps each league WSGI app with `DispatcherMiddleware` (`app/perfect_squad_mount.py`). The GM nav shows **Perfect Squad** when `PERFECT_SQUAD_ROOT` resolves.

## Server layout

```text
/home/you/Boys-Of-Winter-League/     ← this repo (bowlhockey.com WSGI)
/home/you/BOWL-Perfect-Squad/       ← Perfect Squad repo (sibling clone)
```

## Environment (same `.env` / PA web app env as BOWL)

```env
PERFECT_SQUAD_ROOT=/home/you/BOWL-Perfect-Squad
PERFECT_SQUAD_DATABASE_URL=mysql+pymysql://...
SITE_DATABASE_URL=mysql+pymysql://...   # shared with BOWL
SECRET_KEY=...                          # same as BOWL (shared session)
PS_ECONOMY_LIVE=1
AP_ECONOMY_MULTIPLIER=10
```

Install Perfect Squad dependencies on the server:

```bash
pip install -r /home/you/BOWL-Perfect-Squad/requirements.txt
```

After deploy, touch WSGI (see `scripts/STEP2_pythonanywhere.py`).

## One-time economy (site DB backup first)

Run from the **Perfect Squad** repo on the server:

```bash
python scripts/rebase_ap_economy.py --apply --confirm
python scripts/rebase_ap_catalog.py --include-fantasy --apply --confirm
```

## Local dev

Set `PERFECT_SQUAD_ROOT` to your Desktop clone (or rely on the OneDrive Desktop fallback). Run `python run.py` in **BOWL** repo — uses `wsgi.py` combined app.
