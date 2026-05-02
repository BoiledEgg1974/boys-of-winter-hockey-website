# Reference: website event_key → default channel_key (Admin → Discord integration).
# See docs/DISCORD_SITE_TO_BOT.md for when the site enqueues each event.
#
# Bots map channel_key → Discord text channel name via EVENT_CHANNEL_BY_KEY.
#
# Required Discord channel names (create in server or change route map):
#   league-news, transactions, league-announcements, staff-ops-alerts,
#   standings, goals-assists-points, power-rankings, prospect-rankings,
#   positional-rankings, calder-trophy
#
# Manual !commands (Fantasy/Cap): powerrank, prospectrank, positionalrank, calder
# Historical: !powerrank, !prospectrank, !positionalrank, !calder
#
# Shared secret: DISCORD_EVENTS_SHARED_SECRET — default in repo is bowluniverse; override in production.
