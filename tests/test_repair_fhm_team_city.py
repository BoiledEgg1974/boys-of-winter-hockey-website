from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from app.db_utils import repair_fhm_team_city_from_name


def test_repair_fhm_team_city_only_updates_mismatched_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "league.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE teams (
                    id INTEGER PRIMARY KEY,
                    fhm_team_id INTEGER,
                    name TEXT,
                    city TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO teams (id, fhm_team_id, name, city) VALUES
                (1, 101, 'Montreal', 'Montreal'),
                (2, 102, 'Toronto', 'Tor'),
                (3, NULL, 'Legacy', 'Legacy')
                """
            )
        )

    repair_fhm_team_city_from_name(engine)
    repair_fhm_team_city_from_name(engine)

    with engine.connect() as conn:
        rows = {
            int(r.id): (r.name, r.city)
            for r in conn.execute(text("SELECT id, name, city FROM teams ORDER BY id")).all()
        }

    assert rows[1] == ("Montreal", "Montreal")
    assert rows[2] == ("Toronto", "Toronto")
    assert rows[3] == ("Legacy", "Legacy")
