# JSON / HTML parity checklist (GM + draft + hub)

Use this when deciding which flows stay **WebView-only** vs which deserve **native or JSON** endpoints. Paths are relative to each **league site origin** unless noted as **hub** (path prefix `/` on the hub app).

Legend:

- **HTML** — server-rendered template, usually form POST + redirect + flash.
- **JSON** — `application/json` request/response (or JSON body with CSRF where noted).
- **login** — Flask `login_required` on the handler (hub routes listed separately).

## Hub auth (`app/routes/hub_auth.py`, hub origin)

| Path | Methods | login | Response | Mobile notes |
|------|---------|-------|----------|--------------|
| `/register` | GET | no | HTML | Account creation; form POST. |
| `/register` | POST | no | HTML | Same-site cookies after redirect to login. |
| `/login` | GET, POST | no | HTML | Session cookie; `next` query supported. |
| `/forgot-password`, `/reset-password/<token>` | GET, POST | no | HTML | Password reset flow. |
| `/logout` | POST | no | HTML | Clears session. |
| `/account` | GET | yes | HTML | Profile hub page. |
| `/account/...` admin membership tools | GET, POST | yes | HTML | Hub-only admin for memberships/bans. |

**Parity gap:** no JSON login or token API; mobile Phase 1 relies on **WebView login** to the hub (or embedded login page) so the Flask session cookie is set, then league sites load with that identity.

## Draft hub (`app/routes/draft_hub.py`, prefix `/draft-hub`)

| Path | Methods | login | Response | Mobile priority |
|------|---------|-------|----------|-----------------|
| `` | GET | no | HTML | Draft room page. |
| `/archive`, `/archive/<id>` | GET | no | HTML | History. |
| `/api/state` | GET | no | **JSON** | Live board; WebView + polling already viable. |
| `/api/ai-advice` | GET | no | **JSON** | Assistant payload. |
| `/api/eligible-page` | GET | no | **JSON** | Eligible player paging. |
| `/pick` | POST | yes | **HTML** redirect | **v1 mobile:** needs JSON variant or WebView form for pick submission (CSRF form field today). |
| `/pause-timer`, `/resume-timer`, `/auto-complete`, `/end-draft-early` | POST | yes | **HTML** redirect | Commissioner/admin; WebView or future JSON. |
| `/queue/add`, `/queue/remove` | POST | yes | **HTML** redirect | GM queue; same CSRF + form pattern. |
| `/admin/swap-slots` | POST | yes | **JSON** | Commissioner slot swap (already JSON + CSRF in body). |
| `/sound/<id>` | GET | no | Binary / audio | Draft SFX. |

## Expansion draft hub (`app/routes/expansion_draft_hub.py`, prefix `/expansion-draft-hub`)

| Path | Methods | login | Response | Mobile priority |
|------|---------|-------|----------|-----------------|
| `` | GET | no | HTML | Room page. |
| `/api/state` | GET | no | **JSON** | Live state. |
| `/api/eligible-page` | GET | no | **JSON** | Eligible list. |
| `/pick` | POST | yes | **HTML** redirect | Same parity gap as main draft `/pick`. |
| `/pause-timer`, `/resume-timer`, `/end-draft-early` | POST | yes | **HTML** redirect | Admin/timer controls. |

## GM portal (`site_gm_bp`, no URL prefix — `app/routes/site_portal.py`)

Representative **login** routes (all `@login_required` unless noted):

| Path | Methods | Response | JSON / API notes |
|------|---------|----------|------------------|
| `/action-points` | GET | HTML | |
| `/action-points/redeem` | POST | HTML redirect | |
| `/league-news` | GET, POST | HTML | GM-facing news tooling. |
| `/trade-tool` | GET | HTML | Trade builder UI. |
| `/operations/trade-tool/assets` | GET | **JSON** | Asset list for trade UI. |
| `/operations/trade-tool/submit` | POST | **JSON** | Submit trade package (`csrf_token` in JSON body). |
| `/ai-trade-tool` | GET | HTML | |
| `/operations/ai-trade-tool/evaluate` | POST | **JSON** | AI evaluation (`csrf_token` in JSON). |
| `/draft-lottery`, `/boost-lottery` | GET | HTML | |
| `/operations/trade-proposal/<id>` | GET | HTML | Proposal detail. |
| `/operations/trade-proposal/<id>/respond` | POST | HTML redirect | Accept/counter/etc.; **high value** for JSON parity. |
| `/gm-messages` | GET | HTML | Inbox. |
| `/gm-messages/notifications/<id>/open` | GET | redirect | Marks read. |
| `/gm-messages/with/<peer_user_id>` | GET, POST | HTML | Thread + send message; **high value** for JSON parity. |

## Admin / commissioner (`site_admin_bp`, prefix `/admin`)

All listed routes are **`@login_required`** and overwhelmingly **HTML + form POST** (control center, operations queue, news moderation, catalog, draft hub admin, expansion draft admin, Discord integration, etc.).

**Mobile policy:** do not build parallel native admin. Use **WebView / system browser** to `/admin/…` and invest in **responsive admin CSS** (see `body.admin-compact-layout` in `app/static/css/site.css`).

## Existing public JSON API (`app/routes/api.py`, prefix `/api`)

Already useful for widgets / shell: player search, hover cards, box score, game preview, homepage summary, playoff bracket, news vote/comment, Discord bot helpers, **and** `POST /api/mobile/push-token` (device registration for Phase 2 push).

**CSRF:** `api_bp` is registered with `csrf.exempt(api_bp)` in `app/__init__.py`; JSON POSTs validate auth in-route (and hub-origin secrets for Discord bot routes). **Portal HTML POSTs** still use WTForms / `csrf_token` form fields or JSON `csrf_token` where implemented (trade submit, AI evaluate, draft swap).

## Suggested v1 parity order (from roadmap)

1. **Draft / expansion** — `GET /api/state` (done); add JSON POST for pick + queue mutate **or** rely on WebView with invisible form bridge.  
2. **Trades** — proposal respond + list payloads.  
3. **GM messages** — thread fetch + send.  
4. **Operations queue** (GM-facing reads) — optional JSON for mobile dashboard later.

Keep this file updated when you add a JSON twin for an HTML POST so mobile and automation stay aligned.
