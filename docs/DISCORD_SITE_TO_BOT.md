# Website → Discord: what can be delivered

Events are queued in the shared site database (`instance/site_membership.db`) and consumed by **`league_discord_bot`** via `GET /api/discord/events/pending` (header `X-Discord-Events-Secret`).

Each league mount configures routes under **Admin → Discord integration** (`/<league-slug>/admin/discord-integration`).

## API response fields

Each pending event includes:

| Field | Source |
|-------|--------|
| `event_key` | Route / enqueue |
| `channel_key` | Legacy label (logging) |
| `discord_channel_id` | Admin route map |
| `guild_id` | Admin bot connection |
| `payload` | JSON body for embed formatting |

Ack: `POST /api/discord/events/<id>/ack` — marks sent and records `source_type` + `source_id` in `discord_delivered_sources`.

## Auto-enqueued events

| Event key | Default channel key | When the site enqueues |
|-----------|---------------------|-------------------------|
| `gm_news_published` | `team-news` | News **moderation** approve (GM / member submissions from the hub) |
| `admin_news_published` | `league-news` | **Admin compose** league article publish |
| `news_published` | `league-news` | Legacy key (still seeded); **nothing new** is enqueued to this unless you wire it |
| `ap_redemption_posted` | `ap-redemptions` | AP request approved |
| `staff_transaction_posted` | `staff-hirings-firings` | Staff hire or fire request approved |
| `trade_request` | `transactions` | Ops queue status change (non-blocked) |
| `announcement_posted` | `league-announcements` | Commissioner announcement create |
| `draft_hub_pick_made` | `draft-discussion` | Every recorded pick on **live Draft Hub** (GM / admin / auto-queue) |
| `draft_hub_on_clock` | `draft-discussion` | On-clock ping when a new pick timer starts (rounds 1–2) |
| `draft_hub_on_deck` | `draft-discussion` | On-deck alert (when enabled on the draft) |
| `draft_hub_completed` | `draft-discussion` | Draft completion recap |
| `expansion_draft_pick_made` | `expansion-draft` | Every recorded pick on **live Expansion Draft Hub** |
| `expansion_draft_on_clock` | `expansion-draft` | On-clock ping when a new expansion pick timer starts |
| `expansion_draft_completed` | `expansion-draft` | Expansion draft completion recap |
| `story_published` | `league-news` | Story automation live dispatch |
| `control_center_restore` | `staff-ops-alerts` | Control Center backup restore succeeds |
| `bowl_six_leaders_update` | `bowl-six-leaders` | BOWL Six top performers + GM week/season leaders (first post, then **edit** same message) |
| `playoff_bracket_update` | `playoff-bracket` | Live playoff bracket — **one message per series**, each **edited in place** when scores change after import or bot poll |
| `game_boxscore` | `boxscores` (per-team IDs) | Newly **final** games after FHM import — scoreline, SOG, PP, top performers, goalies, three stars + site link posted into **both** participating franchise channels |

Payloads include `source_type` and `source_id` for idempotency where applicable.

**BOWL Six leaders:** Queued when slate scores/stats change (hub load, import, control center, and **bot poll** every ~60s via a lightweight leaders refresh). Configure the route on each league’s **Admin → Discord integration**. The bot **PATCH**es the same embed when `payload.edit_message_id` is set (no new message at the bottom of the channel). Set `BOWL_SIX_DISCORD_POLL_AUTO_UPDATE=1` only if you also want full slate finalization on every poll.

**Playoff bracket:** Queued when [`playoff_bracket_cache_fingerprint`](app/services/playoff_bracket.py) changes after **playoffs have started** (imported playoff games with results or scheduled games on/before today). **Not** queued for regular-season projected brackets from standings. Triggers: **CSV import** (`refresh_after_import`) and **bot poll** (same ~60s cadence as BOWL Six refresh). Map route **`playoff_bracket_update`** → `#playoff-bracket` on each league’s Discord Integration page. The bot posts one message per series; on updates it **PATCH**es each series message using stored message IDs (same pattern as BOWL Six, but multi-message). Events are marked **sent** only when at least one series message is delivered; projection-only payloads stay **pending/failed**. Use **Post fresh playoff bracket** on Discord Integration to clear stored edit targets and queue new posts.

**Game boxscores (per-team channels):** Enable the master **`game_boxscore`** route on **Admin → Discord Integration**, then paste one Discord channel snowflake per franchise under **Team boxscore channels**. After each FHM import, games that newly become final enqueue two outbound events (home + away) with `source_type=game_boxscore` / `source_id={game_id}:{team_id}`.

With the default **`deploy-db`** site update (local import → upload league SQLite), local Discord routes are usually blank, so the importer also writes newly-final game ids to `instance/.deploy_discord_finals/<slug>.json`. After the DBs are promoted on PythonAnywhere, `scripts/notify_discord_after_db_deploy.py` drains those ids against the **live** site DB (real channel map) and refreshes BOWL Six leaders. If no sidecar is present, it falls back to queuing undelivered finals from the last 7 in-game days.

Delivery resolves `discord_channel_id` from the team row (the master route ID is unused — enable/disable only). Blank team channel IDs are skipped; unfinished finals stay pending until a franchise channel is set. Use **Queue recent boxscores** on the same page to manually enqueue finals from the last N in-game days. Check **Force re-post** to clear already-sent locks and queue fresh messages (expanded SOG / PP / leaders / goalies format). Expansion franchises appear automatically when they have current-season standings.

**Sim log (`#sim-log`):** After admin **EXPORT** in the AP ledger, the site enqueues a **closed** export recap (`sim_cycle_update`) from GM export attendance for that date. The news-bot posts that embed in `#sim-log`. **Live** in-progress boards are posted by the FTP bot, not news-bot. The site still polls `#gm-export-tracker` for live export tracking.

**Playoff predictions (`/predict`):** Admin-only slash command in `#playoff-predictions`. When only one round still needs predictions, bare `/predict` queues it automatically. With multiple open rounds, the command lists them and asks you to pick **round** from the menu (`first`, `second`, `conference`, `championship`, or `all`). Re-register slash commands after deploy so Discord sends the `round` option: `python -m scripts.league_discord_bot.register_slash_commands`.

**Expansion Draft slash commands:** Map `expansion_draft_command_list` → `#expansion-draft` for `/expansionlist`; map `expansion_draft_command_pick` → `#expansion-draft-pick` for `/expansionpick`. `/expansionstatus` works in any channel (like `/draftstatus`). Re-register slash commands after deploy: `python -m scripts.league_discord_bot.register_slash_commands`.

**Historical example (Discord server guild `1218341313208914002`):** set that guild ID under Bot connection on `bowl-historical`; map channel snowflakes roughly as: `announcement_posted` → `#announcements`, `ap_redemption_posted` → `#ap-repemptions` (or `#ap-redemptions`), `gm_news_published` → `#team-news`, `admin_news_published` → `#league-news`, `draft_hub_pick_made` / `draft_hub_on_clock` / `draft_hub_completed` → `#draft-discussion`, `expansion_draft_pick_made` / `expansion_draft_on_clock` / `expansion_draft_completed` / `expansion_draft_command_list` → `#expansion-draft`, `expansion_draft_command_pick` → `#expansion-draft-pick`.

## Smoke tests

Use **Queue test event** on **Admin → Discord integration** against any configured route (`event_key` must match `[a-z][a-z0-9_]{0,63}`). Add custom routes with **Add route** if needed.

On app startup, default routes and bot config rows are created for **bowl-historical**, **bowl-fantasy**, and **bowl-cap** with blank guild/channel IDs until you fill them in per league.

## Shared secret

Set `DISCORD_EVENTS_SHARED_SECRET` on the website and bot worker. Default in code is `bowluniverse` when unset — override in production.

## Bot worker

See [DISCORD_BOT_SETUP.md](DISCORD_BOT_SETUP.md). The bot does **not** scrape pages; it only delivers queued events using admin-configured channel IDs.
