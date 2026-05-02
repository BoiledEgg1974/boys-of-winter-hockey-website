# Website → Discord: what can be delivered

Events are queued in the site database and consumed by your league bot via `GET /api/discord/events/pending` (with header `X-Discord-Events-Secret`). Each event has an **event_key** and a **channel_key** (routed per league under **Admin → Discord integration**).

## Currently implemented (site may enqueue)

| Event key | Default channel key | When the site enqueues it |
|-----------|---------------------|---------------------------|
| `story_published` | `league-news` | Story automation live dispatch succeeds |
| `trade_request` | `transactions` | Ops queue request status changes (non-blocked update) |
| `announcement_posted` | `league-announcements` | Commissioner posts a site announcement |
| `control_center_restore` | `staff-ops-alerts` | Control Center backup restore succeeds |
| `standings_posted` | `standings` | **Manual:** Admin → Discord integration → “Queue standings event” |
| `statistical_leaders_posted` | `goals-assists-points` | **Manual:** “Queue statistical leaders event” (payload includes `leader_command_keys`) |
| `power_rankings_posted` | `power-rankings` | **Not auto yet** — enqueue from code/admin test when power rankings are wired |
| `prospect_rankings_posted` | `prospect-rankings` | **Not auto yet** |
| `positional_rankings_posted` | `positional-rankings` | **Not auto yet** |
| `calder_trophy_posted` | `calder-trophy` | **Not auto yet** |

Use **Queue test event** on the Discord integration page to exercise any key. To push live data for the four ranking/trophy channels, add `enqueue_discord_event(...)` calls when the corresponding site data updates (or keep using manual/test queue until those hooks exist).

## Shared secret

Default in `app/config.py` is `bowluniverse` when `DISCORD_EVENTS_SHARED_SECRET` is unset. Set the environment variable in production to a unique value and match it on each bot process.

## Bot mapping

Each bot’s `EVENT_CHANNEL_BY_KEY` maps **channel_key** → Discord **text channel name** (e.g. `power-rankings`). Create those channels in Discord or adjust the route map to match your server’s names.
