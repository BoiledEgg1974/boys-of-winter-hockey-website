# AP Catalog Sync (Live -> Local)

Use this when local AP catalog does not match live.

## One-time setup

No setup required beyond this repo; scripts are already included.

## Step 1 (run on live server)

From repo root:

```powershell
scripts\export_ap_catalog.cmd
```

This creates `ap_redemption_catalog_live.json` in repo root.

Download/copy that JSON file to your local repo root.

## Step 2 (run on local machine)

From local repo root:

```powershell
scripts\import_ap_catalog.cmd
```

This replaces local `ap_redemption_catalog` rows with the JSON data.
By default it also creates a local DB backup:

- `instance/site_membership.db.bak`

## Verify (optional)

```powershell
python -c "import sqlite3; c=sqlite3.connect('instance/site_membership.db'); print(c.execute('select count(*) from ap_redemption_catalog').fetchone()[0]); c.close()"
```

Or compare exact row content against a snapshot JSON:

```powershell
scripts\verify_ap_catalog_sync.cmd
```

It exits with code `0` when matching, `1` when different, and prints missing/extra rows.

## When to run this

- After changing AP catalog on live admin.
- Before testing AP-related features locally.
- Anytime local/live AP items look different.

## Advanced options

Export with custom output path:

```powershell
python scripts/export_ap_catalog.py --out ap_catalog_2026-04-27.json
```

Import from a custom file:

```powershell
python scripts/import_ap_catalog.py --in ap_catalog_2026-04-27.json
```

Verify against a custom file/path:

```powershell
python scripts/verify_ap_catalog_sync.py --in ap_catalog_2026-04-27.json --db instance/site_membership.db
```

Skip creating backup on import:

```powershell
python scripts/import_ap_catalog.py --no-backup
```
