# Mobile auth, CSRF, and session idle timeout

## Phase 1 (Capacitor + WebView) — recommended

**Mechanism:** Flask-Login **server session** + **HTTP-only session cookie** inside the trusted WebView, same as desktop Safari/Chrome.

**Why:** Zero new auth surface area; all existing `login_required` checks, password reset, and membership gates continue to apply. The user logs in on the **hub** (or a league origin if you expose login there), then navigates to league URLs with the same identity as long as the cookie domain/path matches your deployment.

**CSRF:** HTML forms in the WebView include the standard CSRF hidden field. For hybrid JSON calls from injected JavaScript, mirror the patterns already used on the site (`csrf_token` in JSON body for trade submit, AI evaluate, draft admin swap).

## Phase 2+ (optional API-first screens)

If you add **JWT or opaque bearer tokens** for native screens:

- Use **short-lived access** + **refresh** with rotation and server-side revoke list stored in the site DB.  
- **Do not** send refresh tokens to third-party analytics.  
- Align **password change** and **logout everywhere** with token invalidation.  
- JSON POSTs that mutate GM state must still enforce the same authorization rules as `site_portal` (membership, team ownership, commissioner checks).

## Idle timeout (already in Flask)

`create_app` registers a `before_request` handler that marks the session **permanent** and touches it for authenticated users (`_idle_timeout_touch_session` in `app/__init__.py`). Flask’s **PERMANENT_SESSION_LIFETIME** (and your hosting cookie max-age) therefore behaves like a **sliding idle window** in production.

**Mobile implication:** backgrounded WebViews may pause JavaScript; the session can expire while the app is suspended. The shell should **detect 401/HTML login** on navigation and route the user through login again (or open the hub login in an in-app browser).

## Cookies vs third-party context

If the WebView loads a **different registrable domain** than where the cookie was set, you may hit third-party cookie restrictions. Prefer:

- Same **site** cookie `Domain` for hub + leagues if your DNS supports it, or  
- Login **per league origin** if each league is a separate host without a shared superdomain.

Document your production hostnames here when you lock deployment.

## Push token registration

`POST /api/mobile/push-token` (see [PUSH_AND_DEEP_LINKS.md](./PUSH_AND_DEEP_LINKS.md)) requires an **authenticated** session on a **league** app instance (`LEAGUE_SLUG` set). It does not replace login; it only associates FCM/APNs tokens with `(user_id, league_slug, platform)`.
