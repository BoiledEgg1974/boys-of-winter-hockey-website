"""Inspect BOWL Six Discord state for a league (local site DB)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "instance" / "site_membership.db"
LEAGUE = "bowl-historical"


def main() -> None:
    if not DB.exists():
        print(f"No DB at {DB}")
        return
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print("=== bot config ===")
    for r in cur.execute(
        "SELECT league_slug, guild_id, is_enabled FROM discord_league_bot_config WHERE league_slug=?",
        (LEAGUE,),
    ):
        print(dict(r))
    print("=== bowl-six route ===")
    for r in cur.execute(
        "SELECT event_key, channel_key, discord_channel_id, is_enabled FROM discord_channel_routes "
        "WHERE league_slug=? AND event_key='bowl_six_leaders_update'",
        (LEAGUE,),
    ):
        print(dict(r))
    print("=== recent events ===")
    for r in cur.execute(
        "SELECT id, status, created_at, sent_at, last_error, payload_json "
        "FROM discord_outbound_events WHERE league_slug=? AND event_key='bowl_six_leaders_update' "
        "ORDER BY id DESC LIMIT 3",
        (LEAGUE,),
    ):
        d = dict(r)
        payload = json.loads(d.pop("payload_json") or "{}")
        d["edit_message_id"] = payload.get("edit_message_id")
        d["post_new_message"] = payload.get("post_new_message")
        print(d)
    print("=== slate discord fields ===")
    for r in cur.execute(
        "SELECT id, week_start, status, discord_leaders_message_id, discord_leaders_channel_id "
        "FROM bowl_six_slates WHERE league_slug=? ORDER BY week_start DESC LIMIT 8",
        (LEAGUE,),
    ):
        print(dict(r))


if __name__ == "__main__":
    main()
