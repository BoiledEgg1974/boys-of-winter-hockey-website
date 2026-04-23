"""
Load demonstration data so pages render before real FHM CSV imports.
Run from project root:  python scripts/seed_demo.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.db_utils import rebuild_player_fts  # noqa: E402
from app.models import (  # noqa: E402
    Draft,
    DraftPick,
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAward,
    HistoryChampion,
    Player,
    PlayerGoalieStat,
    PlayerSkaterStat,
    Prospect,
    ScoringEvent,
    Season,
    Team,
    TeamStanding,
    db,
)


def run() -> None:
    app = create_app()
    with app.app_context():
        team_count = db.session.scalar(select(func.count()).select_from(Team))
        if team_count and os.environ.get("SEED_FORCE") != "1":
            print("Database already has teams. Set SEED_FORCE=1 to re-seed (clears demo tables).")
            return

        if os.environ.get("SEED_FORCE") == "1":
            for model in (
                ScoringEvent,
                GameSkaterStat,
                GameGoalieStat,
                Game,
                DraftPick,
                Draft,
                Prospect,
                HistoryAward,
                HistoryChampion,
                PlayerGoalieStat,
                PlayerSkaterStat,
                TeamStanding,
                Player,
                Season,
                Team,
            ):
                db.session.execute(delete(model))
            db.session.commit()
            print("Cleared existing demo-related rows.")

        teams_data = [
            ("frost", "Anchorage Frost", "ANC", "#0c4a6e", "#0369a1"),
            ("aurora", "Fairbanks Aurora", "FBK", "#4c1d95", "#7c3aed"),
            ("ridge", "Juneau Ridge", "JNU", "#14532d", "#22c55e"),
            ("tidewater", "Kodiak Tidewater", "KOD", "#1e3a8a", "#3b82f6"),
            ("summit", "Nome Summit", "NOM", "#78350f", "#d97706"),
            ("harbor", "Sitka Harbor", "SIT", "#881337", "#e11d48"),
        ]
        teams = []
        for slug, name, abbr, pc, sc in teams_data:
            t = Team(
                slug=slug,
                name=name,
                abbreviation=abbr,
                city=name.split()[0],
                nickname=name.split()[-1],
                fhm_team_id=f"demo-{slug}",
                primary_color=pc,
                secondary_color=sc,
            )
            db.session.add(t)
            teams.append(t)
        db.session.flush()

        season = Season(
            label="2025–26",
            start_year=2025,
            end_year=2026,
            is_current=True,
            fhm_season_id="demo-s1",
        )
        db.session.add(season)
        db.session.flush()

        players = []
        roster_specs = [
            ("Alex", "McKinnon", "C", 0),
            ("Brady", "Larsson", "LW", 0),
            ("Casey", "Nguyen", "D", 0),
            ("Dylan", "Brooks", "G", 0),
            ("Evan", "Sato", "RW", 1),
            ("Finn", "Okafor", "C", 1),
        ]
        for fn, ln, pos, ti in roster_specs:
            p = Player(
                first_name=fn,
                last_name=ln,
                full_name=f"{fn} {ln}",
                position=pos,
                fhm_player_id=f"demo-p-{fn.lower()}",
                current_team_id=teams[ti].id,
                nationality="USA",
            )
            db.session.add(p)
            players.append(p)
        db.session.flush()

        for i, t in enumerate(teams):
            w = 6 + (i % 3)
            l = 3
            otl = 1 if i % 2 else 0
            db.session.add(
                TeamStanding(
                    season_id=season.id,
                    team_id=t.id,
                    gp=w + l,
                    w=w,
                    l=l,
                    otl=otl,
                    pts=13 + i * 2,
                    gf=32 + i,
                    ga=28 + i,
                    streak="W2" if i % 2 else "L1",
                    conference="Western" if i < 3 else "Eastern",
                    division="North" if i % 2 else "South",
                )
            )

        g1 = Game(
            season_id=season.id,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
            game_date=date(2025, 11, 2),
            home_score=4,
            away_score=3,
            status="final",
            went_to_overtime=True,
            fhm_game_id="demo-g1",
            home_shots=34,
            away_shots=29,
        )
        g2 = Game(
            season_id=season.id,
            home_team_id=teams[2].id,
            away_team_id=teams[3].id,
            game_date=date(2025, 11, 4),
            home_score=2,
            away_score=5,
            status="final",
            fhm_game_id="demo-g2",
            home_shots=30,
            away_shots=41,
        )
        db.session.add_all([g1, g2])
        db.session.flush()

        db.session.add(
            ScoringEvent(
                game_id=g1.id,
                period=1,
                time_elapsed="12:30",
                scorer_player_id=players[0].id,
                assist1_player_id=players[1].id,
                scoring_team_id=teams[0].id,
                strength="EV",
            )
        )
        db.session.add(
            ScoringEvent(
                game_id=g1.id,
                period=3,
                time_elapsed="04:02",
                scorer_player_id=players[4].id,
                scoring_team_id=teams[1].id,
                strength="PP",
            )
        )

        for p, g, a, pid, tid in [
            (2, 1, 1, players[0].id, teams[0].id),
            (0, 2, 0, players[4].id, teams[1].id),
        ]:
            db.session.add(
                GameSkaterStat(
                    game_id=g1.id,
                    player_id=pid,
                    team_id=tid,
                    goals=g,
                    assists=a,
                    shots=4 + p,
                    pim=0,
                )
            )

        db.session.add(
            GameGoalieStat(
                game_id=g1.id,
                player_id=players[3].id,
                team_id=teams[0].id,
                saves=26,
                shots_against=29,
                goals_allowed=3,
                decision="W",
            )
        )

        db.session.add(
            PlayerSkaterStat(
                season_id=season.id,
                player_id=players[0].id,
                team_id=teams[0].id,
                stat_segment="rs",
                gp=12,
                goals=8,
                assists=11,
                points=19,
                pim=4,
                shots=45,
            )
        )
        db.session.add(
            PlayerSkaterStat(
                season_id=season.id,
                player_id=players[4].id,
                team_id=teams[1].id,
                stat_segment="rs",
                gp=12,
                goals=7,
                assists=9,
                points=16,
                pim=6,
                shots=38,
            )
        )
        db.session.add(
            PlayerGoalieStat(
                season_id=season.id,
                player_id=players[3].id,
                team_id=teams[0].id,
                stat_segment="rs",
                gp=10,
                wins=7,
                losses=2,
                otl=1,
                ga=22,
                sa=280,
                so=1,
                gaa=2.2,
                sv_pct=0.921,
            )
        )

        db.session.add(
            Prospect(
                player_id=players[2].id,
                team_id=teams[0].id,
                rank=1,
                tier="A",
                notes="Two-way defender",
            )
        )

        dr = Draft(label="2026 Entry Draft", year=2026, season_id=season.id)
        db.session.add(dr)
        db.session.flush()
        db.session.add(
            DraftPick(
                draft_id=dr.id,
                overall_pick=1,
                round=1,
                team_id=teams[0].id,
                player_id=players[5].id,
            )
        )

        db.session.add(
            HistoryChampion(
                season_id=season.id,
                team_id=teams[0].id,
                trophy="Boys of Winter Cup",
            )
        )
        db.session.add(
            HistoryAward(
                season_id=season.id,
                award_name="MVP",
                player_id=players[0].id,
                team_id=teams[0].id,
            )
        )

        db.session.commit()
        rebuild_player_fts(db.engine)
        print("Demo seed complete. Start the app with: python run.py")


if __name__ == "__main__":
    run()
