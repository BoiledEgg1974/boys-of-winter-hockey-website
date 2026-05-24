from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.discord_interactions import COMMAND_DEFINITIONS


def _register_commands(*, token: str, application_id: str, url: str, scope: str) -> None:
    resp = httpx.put(
        url,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json=COMMAND_DEFINITIONS,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Registered {len(COMMAND_DEFINITIONS)} BOWL slash command(s) for {scope}.")


def main() -> None:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    application_id = os.environ.get("DISCORD_APPLICATION_ID", "").strip()
    guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required")
    if not application_id:
        raise RuntimeError("DISCORD_APPLICATION_ID is required")
    base = f"https://discord.com/api/v10/applications/{application_id}"

    if guild_id:
        _register_commands(
            token=token,
            application_id=application_id,
            url=f"{base}/guilds/{guild_id}/commands",
            scope=f"guild {guild_id}",
        )
        return

    # One bot, three servers: register global commands; the hub interactions URL routes by guild id.
    _register_commands(
        token=token,
        application_id=application_id,
        url=f"{base}/commands",
        scope="global (all servers)",
    )
    print(
        "Set Interactions Endpoint URL to "
        "https://www.bowlhockey.com/api/discord/interactions "
        "(or your SITE_PUBLIC_BASE_URL + /api/discord/interactions)."
    )
    print(
        "Ensure each league's Discord Integration page has the correct Server ID "
        "(guild id) for bowl-historical, bowl-fantasy, and bowl-cap."
    )


if __name__ == "__main__":
    main()
