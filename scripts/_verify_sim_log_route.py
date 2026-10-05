import sqlite3

c = sqlite3.connect(r"instance/site_membership.db")
row = c.execute(
    "SELECT discord_channel_id FROM discord_channel_routes "
    "WHERE league_slug='bowl-fantasy' AND event_key='sim_cycle_update'"
).fetchone()
bot = c.execute(
    "SELECT guild_id FROM discord_league_bot_config WHERE league_slug='bowl-fantasy'"
).fetchone()
print("sim_cycle_update channel", row)
print("guild", bot)
c.close()
