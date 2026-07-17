import sqlite3
from pathlib import Path

db = Path(r"C:\Users\keeno\Projects\Boys-Of-Winter-League\instance\league3.db")
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("SEASONS:")
for r in cur.execute(
    "select id, start_year, end_year, is_current from seasons order by start_year desc limit 8"
):
    print(dict(r))

cur.execute("select id, start_year from seasons where is_current=1")
row = cur.fetchone()
print("CURRENT", dict(row) if row else None)
sid = (
    row["id"]
    if row
    else cur.execute("select id from seasons order by start_year desc limit 1").fetchone()[0]
)
print("Using season", sid)

q = """
select t.id, t.slug, t.abbreviation, t.name, t.fhm_team_id, t.fhm_conference_id, t.fhm_division_id,
       s.conference, s.division, s.gp, s.w, s.l, s.otl, s.ties, s.shootout_wins, s.shootout_losses,
       s.pts, s.gf, s.ga, s.win_pct
from team_standings s join teams t on t.id=s.team_id
where s.season_id=? and t.abbreviation in ('OTT','BOS')
"""
print("OTT/BOS:")
for r in cur.execute(q, (sid,)):
    print(dict(r))

print("conference values:")
for r in cur.execute(
    "select conference, count(*) c from team_standings where season_id=? group by conference",
    (sid,),
):
    print(dict(r))

print("division values:")
for r in cur.execute(
    "select division, count(*) c from team_standings where season_id=? group by division order by c desc",
    (sid,),
):
    print(dict(r))

print(
    "team count",
    cur.execute("select count(*) from team_standings where season_id=?", (sid,)).fetchone()[0],
)

print("games by status:")
for r in cur.execute(
    "select status, count(*) c from games where season_id=? group by status", (sid,)
):
    print(dict(r))

print("non-final game types:")
for r in cur.execute(
    """
    select game_type, count(*) c from games
    where season_id=? and ifnull(status,'')!='final'
    group by game_type order by c desc limit 15
    """,
    (sid,),
):
    print(dict(r))

print("top standings by pts:")
for r in cur.execute(
    """
    select t.abbreviation, s.w, s.l, s.otl, s.pts, s.gp, s.conference, s.division,
           (s.w+s.l+ifnull(s.ties,0)+ifnull(s.shootout_wins,0)+ifnull(s.shootout_losses,0)) as gp_disp
    from team_standings s join teams t on t.id=s.team_id
    where s.season_id=?
    order by s.pts desc, s.w desc
    limit 20
    """,
    (sid,),
):
    print(dict(r))
