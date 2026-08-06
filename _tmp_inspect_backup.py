import json
import sqlite3
from pathlib import Path

SITE = Path("instance/full_backups/20260714T090731Z/site/site_membership.db")
CAP = Path("instance/full_backups/20260714T090731Z/league/bowl-cap.db")
# also current league3
CAP2 = Path("instance/league3.db")

for label, path in [("backup_site", SITE), ("backup_cap", CAP), ("league3", CAP2)]:
    print("====", label, path.exists(), path.stat().st_size if path.exists() else 0)

site = sqlite3.connect(SITE)
site.row_factory = sqlite3.Row
cap = sqlite3.connect(CAP)
cap.row_factory = sqlite3.Row

print("\nCap DET/ATL teams in backup:")
for r in cap.execute(
    "SELECT id, abbreviation, name, nickname, fhm_team_id FROM teams WHERE id IN (5,28) OR abbreviation IN ('DET','ATL')"
):
    print(dict(r))

print("\nCap memberships DET/ATL:")
for r in site.execute(
    """
    SELECT m.id, m.user_id, m.team_id, m.fhm_team_id, m.status,
           u.username, u.discord_name, u.discord_user_id
    FROM gm_league_memberships m
    LEFT JOIN site_users u ON u.id = m.user_id
    WHERE m.league_slug='bowl-cap'
      AND (m.team_id IN (5,28) OR CAST(m.fhm_team_id AS TEXT) IN ('9','227','5','28'))
    """
):
    print(dict(r))

print("\nAll Cap memberships with fhm mismatches:")
teams = {r["id"]: str(r["fhm_team_id"] or "") for r in cap.execute("SELECT id, fhm_team_id FROM teams")}
mems = site.execute(
    "SELECT id, user_id, team_id, fhm_team_id, status FROM gm_league_memberships WHERE league_slug='bowl-cap'"
).fetchall()
print("cap membership count", len(mems))
for m in mems:
    exp = teams.get(m["team_id"], "")
    act = str(m["fhm_team_id"] or "")
    if exp and act and exp != act:
        print("MISMATCH", dict(m), "expected", exp)
    if act in ("9", "227") or m["team_id"] in (5, 28):
        print("DET/ATL related", dict(m), "expected_fhm", exp)

print("\nRecent Cap DET articles:")
for r in site.execute(
    """
    SELECT id, team_id, title, author_user_id, published_at
    FROM news_articles
    WHERE league_slug='bowl-cap' AND (team_id=5 OR title LIKE '%Detroit%')
    ORDER BY id DESC LIMIT 15
    """
):
    print(dict(r))

print("\nRecent Cap discord news payloads involving DET/ATL:")
for r in site.execute(
    """
    SELECT id, event_key, status, payload_json, created_at
    FROM discord_outbound_events
    WHERE league_slug='bowl-cap' AND event_key LIKE '%news%'
    ORDER BY id DESC LIMIT 30
    """
):
    p = json.loads(r["payload_json"] or "{}")
    tid = p.get("team_id")
    fhm = p.get("fhm_team_id")
    title = str(p.get("title") or "")
    if tid in (5, 28) or fhm in (9, 227, "9", "227") or "Detroit" in title or "Atlanta" in title:
        print(
            {
                "id": r["id"],
                "event_key": r["event_key"],
                "status": r["status"],
                "team_id": tid,
                "fhm": fhm,
                "abbrev": p.get("team_abbrev"),
                "mention": p.get("team_gm_mention"),
                "title": title[:80],
                "article_id": p.get("article_id"),
            }
        )
