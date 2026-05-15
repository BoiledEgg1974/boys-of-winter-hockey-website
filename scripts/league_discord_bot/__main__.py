from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.league_discord_bot.client import LeagueDiscordBot
from scripts.league_discord_bot.config import load_settings


def main() -> None:
    bot = LeagueDiscordBot(load_settings())
    bot.run_forever()


if __name__ == "__main__":
    main()
