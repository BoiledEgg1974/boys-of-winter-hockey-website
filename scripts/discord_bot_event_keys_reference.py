# Reference: website event_key → default channel_key (Admin → Discord integration).
# See docs/DISCORD_SITE_TO_BOT.md for when the site enqueues each event.
#
# Delivery uses discord_channel_id from admin (not channel name mapping).
#
# Default channel keys (paste Discord snowflake IDs in admin):
#   team-news, league-news, ap-redemptions, draft-discussion, expansion-draft-discussion,
#   league-announcements, transactions, standings, goals-assists-points,
#   power-rankings, prospect-rankings, positional-rankings, calder-trophy,
#   staff-ops-alerts
#
# Env: DISCORD_EVENTS_SHARED_SECRET, DISCORD_BOT_TOKEN, DISCORD_BOT_LEAGUE_BASE_URLS
# Run worker: python -m scripts.league_discord_bot
