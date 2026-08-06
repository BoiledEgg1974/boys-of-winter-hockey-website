import json
import sqlite3
from pathlib import Path

SITE = Path("instance/site_membership.db")
CAP = Path("instance/league3.db")

site = sqlite3.connect(SITE)
site.row_factory = sqlite3.Row
cap = sqlite3.connect(CAP)
cap.row_factory = sqlite3.Row

print("SITE TABLES:", [r[0] for r in site.execute("SELECT name FROM sqlite_master WHERE type='table'")])

print("=== Cap teams DET/ATL ===")
for r in cap.execute(
    "SELECT id, abbreviation, name, nickname, fhm_team_id, slug FROM teams WHERE id IN (5,28) OR abbreviation IN ('DET','ATL')"
):
    print(dict(r))

user_table = None
for cand in ("user", "users", "site_users", "membership_users"):
    try:
        site.execute(f"SELECT 1 FROM {cand} LIMIT 1")
        user_table = cand
        break
    except Exception:
        pass
print("user_table=", user_table)

print("\n=== All Cap memberships ===")
mems = site.execute(
    """
    SELECT id, user_id, league_slug, team_id, fhm_team_id, status, approved_at
    FROM gm_league_memberships
    WHERE league_slug = 'bowl-cap'
    ORDER BY team_id
    """
).fetchall()
print(f"count={len(mems)}")
for r in mems:
    print(dict(r))

if user_table:
    print("\n=== DET/ATL memberships with users ===")
    q = f"""
    SELECT m.id, m.user_id, m.team_id, m.fhm_team_id, m.status,
           u.username, u.discord_name, u.discord_user_id
    FROM gm_league_memberships m
    LEFT JOIN {user_table} u ON u.id = m.user_id
    WHERE m.league_slug = 'bowl-cap'
      AND (m.team_id IN (5,28) OR CAST(m.fhm_team_id AS TEXT) IN ('9','227','5','28'))
    """
    for r in site.execute(q):
        print(dict(r))

print("\n=== Cap news articles ===")
arts = site.execute(
    """
    SELECT id, league_slug, team_id, title, author_user_id, status, category, published_at
    FROM news_articles
    WHERE league_slug = 'bowl-cap'
    ORDER BY id DESC
    LIMIT 30
    """
).fetchall()
print(f"count shown={len(arts)}")
for r in arts:
    print(dict(r))

print("\n=== Detroit articles + author memberships ===")
det_arts = site.execute(
    """
    SELECT id, team_id, title, author_user_id, published_at
    FROM news_articles
    WHERE league_slug='bowl-cap' AND team_id=5
    ORDER BY id DESC LIMIT 15
    """
).fetchall()
print("det article count", len(det_arts))
for a in det_arts:
    print("article", dict(a))
    auth = a["author_user_id"]
    if auth is None:
        continue
    ms = site.execute(
        """
        SELECT id, team_id, fhm_team_id, status
        FROM gm_league_memberships
        WHERE league_slug='bowl-cap' AND user_id=?
        """,
        (auth,),
    ).fetchall()
    print("  author memberships", [dict(x) for x in ms])

print("\n=== Discord outbound news events for bowl-cap ===")
cols = [c[1] for c in site.execute("PRAGMA table_info(discord_outbound_events)")]
print("event cols", cols)
evs = site.execute(
    """
    SELECT id, league_slug, event_key, status, payload_json, created_at
    FROM discord_outbound_events
    WHERE league_slug = 'bowl-cap'
      AND event_key LIKE '%news%'
    ORDER BY id DESC
    LIMIT 25
    """
).fetchall()
for r in evs:
    d = dict(r)
    try:
        p = json.loads(d.get("payload_json") or "{}")
    except Exception:
        p = {}
    print(
        {
            "id": d["id"],
            "event_key": d["event_key"],
            "status": d["status"],
            "created_at": d["created_at"],
            "team_id": p.get("team_id"),
            "fhm_team_id": p.get("fhm_team_id"),
            "team_abbrev": p.get("team_abbrev"),
            "team_name": p.get("team_name"),
            "team_gm_mention": p.get("team_gm_mention"),
            "title": p.get("title"),
            "article_id": p.get("article_id"),
        }
    )

# Cross-check: for DET articles, what mention would FHM lookup return?
print("\n=== Mention resolution simulation ===")
for team_id, fhm in ((5, "9"), (28, "227")):
    by_fhm = site.execute(
        """
        SELECT m.user_id, m.team_id, m.fhm_team_id, m.status
        FROM gm_league_memberships m
        WHERE m.league_slug='bowl-cap' AND m.fhm_team_id=? AND m.status='active'
        ORDER BY m.approved_at DESC, m.id DESC
        """,
        (fhm,),
    ).fetchall()
    by_pk = site.execute(
        """
        SELECT m.user_id, m.team_id, m.fhm_team_id, m.status
        FROM gm_league_memberships m
        WHERE m.league_slug='bowl-cap' AND m.team_id=? AND m.status='active'
        ORDER BY m.approved_at DESC, m.id DESC
        """,
        (team_id,),
    ).fetchall()
    print(f"team_id={team_id} fhm={fhm}")
    print("  by_fhm", [dict(x) for x in by_fhm])
    print("  by_pk", [dict(x) for x in by_pk])

# Any membership where fhm_team_id doesn't match team.fhm
print("\n=== Membership FHM mismatches vs Cap teams ===")
team_fhm = {r["id"]: str(r["fhm_team_id"]) for r in cap.execute("SELECT id, fhm_team_id FROM teams")}
for m in mems:
    expected = team_fhm.get(m["team_id"])
    actual = str(m["fhm_team_id"] or "")
    if expected and actual and expected != actual:
        print("MISMATCH", dict(m), "expected_fhm", expected)
    if not actual and expected:
        print("MISSING FHM", dict(m), "expected_fhm", expected)
    # also flag if someone has DET pk but ATL fhm or vice versa
    if m["team_id"] == 5 and actual == "227":
        print("DET PK WITH ATL FHM", dict(m))
    if m["team_id"] == 28 and actual == "9":
        print("ATL PK WITH DET FHM", dict(m))
    if actual == "9" and m["team_id"] != 5:
        print("FHM 9 on non-DET membership", dict(m))
    if actual == "227" and m["team_id"] != 28:
        print("FHM 227 on non-ATL membership", dict(m))
