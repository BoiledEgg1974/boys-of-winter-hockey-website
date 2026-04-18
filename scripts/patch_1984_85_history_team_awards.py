"""Repair mistaken bulk patch: 1984-85 team trophies were all set to Vancouver (FHM 280).

Restores ``history_awards`` rows to match ``data/imports/raw/bowl_fantasy/history_awards.csv``:

- Boiledegg's → FHM **280** (Vancouver Giants)
- Prince of Wales / Bowl Cup → FHM **3** (Toronto Maple Leafs)
- Clarence Campbell → FHM **18** (Toronto Six)

Also strips the obsolete ``unresolved_team=…`` note from **1981-82 Prince of Wales** in the CSV
(team **24** = Trois-Rivières Lions in ``team_data.csv``).

Run from repo root:

  PYTHONPATH=. python scripts/patch_1984_85_history_team_awards.py
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import or_, select, update

from app import create_app
from app.models import HistoryAward, Season, Team, db

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "data" / "imports" / "raw" / "bowl_fantasy" / "history_awards.csv"

YEAR = "1984-85"
AWARD_FHM: tuple[tuple[str, str], ...] = (
    ("BOILEDEGG'S TROPHY", "280"),
    ("PRINCE OF WALES TROPHY", "3"),
    ("CLARENCE CAMPBELL TROPHY", "18"),
    ("BOWL CUP TROPHY", "3"),
)


def _year_clause():
    loc = [HistoryAward.notes.ilike(f"%sheet_season={YEAR}%")]
    season = db.session.scalar(select(Season).where(Season.label == YEAR))
    if season:
        loc.append(HistoryAward.season_id == season.id)
    return or_(*loc)


def patch_csv() -> None:
    try:
        text = CSV_PATH.read_text(encoding="utf-8")
    except OSError as e:
        print(f"CSV: skip read ({e})")
        return
    old = text
    text = text.replace(
        "1981-82,PRINCE OF WALES TROPHY,,24,unresolved_team=Trois-Rivieres Lions",
        "1981-82,PRINCE OF WALES TROPHY,,24,",
    )
    text = text.replace("1984-85,PRINCE OF WALES TROPHY,,280,", "1984-85,PRINCE OF WALES TROPHY,,3,")
    text = text.replace("1984-85,CLARENCE CAMPBELL TROPHY,,280,", "1984-85,CLARENCE CAMPBELL TROPHY,,18,")
    text = text.replace("1984-85,BOWL CUP TROPHY,,280,", "1984-85,BOWL CUP TROPHY,,3,")
    if text == old:
        print("CSV: no changes (already repaired or patterns differ).")
        return
    try:
        CSV_PATH.write_text(text, encoding="utf-8", newline="\n")
    except OSError as e:
        print(f"CSV: could not write ({e}). Close the file and retry.")
        return
    print("CSV: repaired 1981-82 PoW note and/or reverted mistaken 1984-85 FHM 280 rows.")


def patch_db(app) -> None:
    with app.app_context():
        yc = _year_clause()
        total = 0
        for award_name, fhm in AWARD_FHM:
            team = db.session.scalar(select(Team).where(Team.fhm_team_id == fhm))
            if not team:
                print(f"DB: skip {award_name!r} — no Team fhm_team_id={fhm!r}.")
                continue
            res = db.session.execute(
                update(HistoryAward)
                .where(
                    HistoryAward.award_name == award_name,
                    yc,
                )
                .values(team_id=team.id)
            )
            total += res.rowcount or 0
        db.session.commit()
        if total == 0:
            print("DB: no rows matched (check season label / sheet_season notes for 1984-85).")
            return
        print(f"DB: updated {total} row(s) for {YEAR} team trophies (per CSV FHM ids).")


def main() -> None:
    app = create_app()
    patch_db(app)
    patch_csv()


if __name__ == "__main__":
    main()
