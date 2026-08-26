import sqlite3
c = sqlite3.connect("instance/league3.db")
cur = c.cursor()
cur.execute("""
SELECT strftime('%Y', game_date) yr, COUNT(*), 
       SUM(CASE WHEN pp_opp_home IS NOT NULL OR pp_opp_away IS NOT NULL THEN 1 ELSE 0 END)
FROM games WHERE game_date IS NOT NULL GROUP BY yr ORDER BY yr DESC LIMIT 10
""")
print("Games by year:", cur.fetchall())
cur.execute("""
SELECT COUNT(*) FROM team_season_records 
WHERE season_year_label IN ('1999-00','2000-01') 
AND pp_pct IS NOT NULL
""")
print("rows with pp_pct:", cur.fetchone())
cur.execute("""
SELECT season_year_label, team_id, ppg, pp_chances, ppg_against, sh_chances, pp_pct, pk_pct, source
FROM team_season_records WHERE season_year_label='1999-00' LIMIT 3
""")
print("1999-00 sample:", cur.fetchall())
cur.execute("""
SELECT season_year_label, team_id, ppg, pp_chances, ppg_against, sh_chances, pp_pct, pk_pct, source
FROM team_season_records WHERE season_year_label='2000-01' LIMIT 3
""")
print("2000-01 sample:", cur.fetchall())
c.close()
