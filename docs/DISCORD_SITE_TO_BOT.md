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
| `trade_request` | `transactions` | Ops queue status change (non-blocked) |
| `announcement_posted` | `league-announcements` | Commissioner announcement create |
| `draft_hub_pick_made` | `draft-discussion` | Every recorded pick on **live Draft Hub** (GM / admin / auto-queue) |
| `expansion_draft_pick_made` | `expansion-draft-discussion` | Every recorded pick on **live Expansion Draft Hub** |
| `story_published` | `league-news` | Story automation live dispatch |
| `control_center_restore` | `staff-ops-alerts` | Control Center backup restore succeeds |

Payloads include `source_type` and `source_id` for idempotency where applicable.

**Historical example (Discord server guild `1218341313208914002`):** set that guild ID under Bot connection on `bowl-historical`; map channel snowflakes roughly as: `announcement_posted` → `#announcements`, `ap_redemption_posted` → `#ap-repemptions` (or `#ap-redemptions`), `gm_news_published` → `#team-news`, `admin_news_published` → `#league-news`, `draft_hub_pick_made` → `#draft-discussion`, `expansion_draft_pick_made` → `#expansion-draft-discussion`.

## Manual / admin triggers (and reserved keys)

Routes for these keys are **seeded by default** so you can paste channel IDs anytime; only some auto-enqueue today:

| Event key | Default channel key | Trigger today |
|-----------|---------------------|----------------|
| `standings_posted` | `standings` | Admin → **Queue standings** |
| `statistical_leaders_posted` | `goals-assists-points` | Admin → **Queue statistical leaders** |
| `power_rankings_posted` | `power-rankings` | Test button / wire in code later |
| `prospect_rankings_posted` | `prospect-rankings` | Test / wire later |
| `positional_rankings_posted` | `positional-rankings` | Test / wire later |
| `calder_trophy_posted` | `calder-trophy` | Test / wire later |

Use **Queue test event** against any configured route for smoke tests (`event_key` must match `[a-z][a-z0-9_]{0,63}`).

## Shared secret

Set `DISCORD_EVENTS_SHARED_SECRET` on the website and bot worker. Default in code is `bowluniverse` when unset — override in production.

## Bot worker

See [DISCORD_BOT_SETUP.md](DISCORD_BOT_SETUP.md). The bot does **not** scrape pages; it only delivers queued events using admin-configured channel IDs.
