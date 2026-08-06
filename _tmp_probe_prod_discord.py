"""Probe production Cap Discord pending + try to infer mention bug via public pages if needed."""
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(".env"))
secret = os.environ.get("DISCORD_EVENTS_SHARED_SECRET", "").strip()
base = "https://www.bowlhockey.com/bowl-cap"

req = urllib.request.Request(
    f"{base}/api/discord/events/pending?league_slug=bowl-cap&limit=50",
    headers={"X-Discord-Events-Secret": secret},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as e:
    print("pending failed:", type(e).__name__, e)
    raise SystemExit(1)

print("ok", data.get("ok"), "bot_enabled", data.get("bot_enabled"), "n", len(data.get("events") or []))
for ev in data.get("events") or []:
    p = ev.get("payload") or {}
    print(
        {
            "id": ev.get("id"),
            "event_key": ev.get("event_key"),
            "team_id": p.get("team_id"),
            "fhm_team_id": p.get("fhm_team_id"),
            "team_abbrev": p.get("team_abbrev"),
            "team_name": p.get("team_name"),
            "team_gm_mention": p.get("team_gm_mention"),
            "title": (p.get("title") or "")[:70],
            "article_id": p.get("article_id"),
        }
    )
