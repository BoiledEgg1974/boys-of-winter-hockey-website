# Push notifications and deep links

## Device token storage (implemented)

- **Table:** `mobile_push_devices` on the **site** SQLite bind (created by `ensure_mobile_push_devices_sqlite` in `app/db_utils.py`).  
- **Columns (conceptual):** `user_id`, `league_slug`, `platform` (`ios` \| `android`), `device_token`, `created_at`, `updated_at`.  
- **Uniqueness:** one row per `(user_id, league_slug, platform)`; upsert refreshes `device_token` when FCM rotates the token.

### HTTP API

`POST /api/mobile/push-token` (Flask blueprint `api`, full path `/api/mobile/push-token` on each league origin)

**Auth:** must be logged in (`current_user.is_authenticated`). **League:** `LEAGUE_SLUG` must be set on the app; returns `400` with `{"error":"no_league"}` on the hub if misconfigured.

**Register / upsert body (JSON):**

```json
{
  "platform": "ios",
  "token": "<fcm_registration_token>"
}
```

**Clear body:** same `platform`, empty `token`:

```json
{
  "platform": "android",
  "token": ""
}
```

**Responses:** `{"ok": true}`, `{"ok": true, "cleared": true}`, or `{"error": "..."}` with appropriate HTTP status (`auth`, `bad_platform`, `bad_token`, `site_db_unavailable`, etc.).

**Note:** Sending push payloads (FCM/APNs), retry policy, and category strings are **not** implemented in this repository slice; add a worker process that reads `mobile_push_devices` and calls your provider.

## Suggested push categories (matrix)

| Event | Suggested category | Deep link target (league origin) |
|-------|-------------------|-----------------------------------|
| Draft on the clock / your turn | `draft` | `/draft-hub` or `/draft-hub/archive/<id>` as appropriate |
| Expansion draft same | `draft` | `/expansion-draft-hub` |
| Trade proposal created / updated | `trades` | `/operations/trade-proposal/<id>` |
| New GM message or thread reply | `messages` | `/gm-messages/with/<peer_user_id>` |
| News tagged to your team (optional) | `news` | Headlines or article URL from existing routes |
| Operations queue decision (GM) | `operations` | GM-facing ops URL you already use in templates |

Prefix all paths with `request.script_root` / `data-application-root` when the app is mounted under a subpath (see static JS `withRoot` patterns).

## Universal links / app links

- **iOS:** Associated Domains + `apple-app-site-association` on each league host.  
- **Android:** Digital Asset Links + `assetlinks.json`.  
- **Patterns:** include at least `/draft-hub/*`, `/expansion-draft-hub/*`, `/operations/trade-proposal/*`, `/gm-messages/*`, and `/admin/*` (admin should still open in **web** surface per product policy).

## Commissioner / admin URLs

Deep links may open `/admin/…` in **SafariViewController**, Chrome Custom Tab, or the system browser so powerful actions stay on the audited web stack.

## Security

- Treat `device_token` as **sensitive**; restrict DB backups and logs.  
- Rate-limit token registration per user in the worker or reverse proxy if abuse appears.  
- When JWT ships for native-only screens, keep push **separate** from API bearer tokens (FCM token is not an auth credential).
