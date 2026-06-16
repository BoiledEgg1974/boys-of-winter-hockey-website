import sqlite3
from pathlib import Path

for name in ("league3.db", "bowl-cap.db"):
    p = Path("instance") / name
    if not p.is_file():
        print(name, "missing")
        continue
    c = sqlite3.connect(p)
    print("===", name, "===")
    for q, label in [
        ("SELECT id, city, nickname, fhm_team_id FROM teams WHERE fhm_team_id = '9'", "fhm 9"),
        ("SELECT id, city, nickname, fhm_team_id FROM teams WHERE id IN (9, 28, 227)", "ids 9,28,227"),
        ("SELECT id, city, nickname, fhm_team_id FROM teams WHERE city = 'Detroit'", "Detroit"),
    ]:
        try:
            print(label, c.execute(q).fetchall())
        except Exception as e:
            print(label, e)
