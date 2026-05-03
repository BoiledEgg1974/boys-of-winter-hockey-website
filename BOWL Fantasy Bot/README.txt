Discord stats bot for BOWL-Fantasy (separate from the website CSV importer).

1. Install Python 3 with pip; run dependencies\dependencies.bat if you use that bundle.
2. Copy .env.example to .env and set DISCORD_BOT_TOKEN (and optional DISCORD_GUILD_ID).
3. Edit the CSV paths near the top of BOWL-Fantasy-Bot.py to match your FHM export folder.
4. From this folder: python BOWL-Fantasy-Bot.py (or use start_bot.ps1).

Website data refresh from CSVs uses scripts\import_data.py from the repo root (see docs\DATA-UPDATE.md).
