# Discord delivery bot setup

One **`league_discord_bot`** process polls all three league sites and posts to each league’s Discord server using channel IDs configured in **Admin → Discord integration**.

## 1. Create the Discord application

1. Open [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** → **Reset Token** → copy token → set as `DISCORD_BOT_TOKEN` (never commit to git).
3. Enable **Message Content Intent** only if you later add inbound features (not required for outbound-only delivery).
4. **OAuth2 → URL Generator**: scopes `bot`, permissions **Send Messages**, **Embed Links** (and **View Channels**).
5. Invite the bot to **each** of the three league Discord servers (Historical, Fantasy, Cap).

## 2. Channel IDs per league

In each league’s admin page (`/<league-slug>/admin/discord-integration`):

1. **Bot connection** — paste the Discord **server (guild) ID**, enable delivery.
2. **Event route map** — for each feed, paste the **channel snowflake** (right-click channel → Copy Channel ID with Developer Mode on).
3. Save routes.

| Feed | Event key | Default channel key |
|------|-----------|---------------------|
| Team news (GM submissions, moderated) | `gm_news_published` | `team-news` |
| League news (admin compose) | `admin_news_published` | `league-news` |
| AP redemptions | `ap_redemption_posted` | `ap-redemptions` |
| Trades / ops | `trade_request` | `transactions` |
| Announcements | `announcement_posted` | `league-announcements` |
| Draft Hub — each live pick | `draft_hub_pick_made` | `draft-discussion` |
| Expansion draft — each live pick | `expansion_draft_pick_made` | `expansion-draft-discussion` |
| Legacy (optional) | `news_published` | `league-news` |

**Optional:** paste the same Discord channel ID onto multiple routes if you want combined feeds into one `#channel`.

**Historical example guild `1218341313208914002`:** on **bowl-historical → Discord integration**, set Bot connection guild to that ID, then paste channel IDs: `#announcements` → `announcement_posted`; `#team-news` → `gm_news_published`; `#league-news` → `admin_news_published`; AP channel → `ap_redemption_posted`; `#draft-discussion` → `draft_hub_pick_made`; `#expansion-draft-discussion` → `expansion_draft_pick_made`.

## 3. Website environment

On the web app (all mounts share `instance/site_membership.db`):

```env
DISCORD_EVENTS_SHARED_SECRET=<long-random-string>
SITE_PUBLIC_BASE_URL=https://www.bowlhockey.com
```

Generate a secret, e.g. PowerShell:

```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
```

## 4. Bot worker environment

```env
DISCORD_BOT_TOKEN=<bot-token>
DISCORD_EVENTS_SHARED_SECRET=<same-as-website>
DISCORD_BOT_POLL_SECONDS=8
DISCORD_BOT_NAME=league-discord-bot
DISCORD_BOT_VERSION=1.0.0
SITE_PUBLIC_BASE_URL=https://www.bowlhockey.com
# Optional explicit bases (overrides SITE_PUBLIC_BASE_URL per slug):
# DISCORD_BOT_LEAGUE_BASE_URLS=bowl-historical:https://www.bowlhockey.com/bowl-historical,bowl-fantasy:https://www.bowlhockey.com/bowl-fantasy,bowl-cap:https://www.bowlhockey.com/bowl-cap
```

## 5. Run the worker

From the repo root (venv with project dependencies installed):

```bash
python -m scripts.league_discord_bot
```

**PythonAnywhere:** create an **Always-on task** with the same command and env vars.

## 6. Team emoji maps

The delivery bot uses `scripts/league_discord_bot/team_maps.py` (same FHM team IDs and custom emoji mentions as BOWL-STATS-BOT). Enqueue payloads include `fhm_team_id` from each league’s `teams.fhm_team_id` so news, AP, and trade posts show the correct server emojis.

## 7. Verify

1. Admin → Discord integration → **Queue test event** for a route with a channel ID.
2. Confirm heartbeat row turns green within ~30s.
3. Confirm message appears in Discord and queue row shows `sent`.

## Duplicate prevention

- Enqueue uses stable `source_type` + `source_id` when content is tied to a DB row (news article, AP request, trade request, announcement).
- `discord_delivered_sources` records successful delivery on ack.
- Re-publishing the same entity does not create a second Discord post.
