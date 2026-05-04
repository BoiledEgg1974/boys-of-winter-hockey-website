"""Repair ``ScoringEvent.period`` using FHM ``boxscore_period_scoring_summary.csv``.

Older imports used ``to_int("OT1", 1) -> 1``, so overtime goals were stored as period 1. This
script matches each CSV row to the corresponding DB row (game FHM id, scorer, assists, team,
clock, strength) and sets ``period`` to the same value as :func:`fhm_scoring_period_to_int`.

Examples::

    PYTHONPATH=. python scripts/repair_scoring_periods_from_fhm_csv.py --league-slug bowl-cap --dry-run
    PYTHONPATH=. python scripts/repair_scoring_periods_from_fhm_csv.py --league-slug bowl-cap

Optional absolute CSV path (defaults to ``<RAW_IMPORT_DIR>/boxscore_period_scoring_summary.csv``)::

    PYTHONPATH=. python scripts/repair_scoring_periods_from_fhm_csv.py --league-slug bowl-cap --csv C:/path/to/boxscore_period_scoring_summary.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import and_, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import league_by_slug, make_league_config  # noqa: E402
from app.models import Game, Player, ScoringEvent, Team, db  # noqa: E402
from scripts.import_pipeline.encoding_utils import (  # noqa: E402
    cell_val,
    fhm_scoring_period_to_int,
    read_csv_normalized,
    to_int,
)


def _fmt_clock_seconds(sec: int | None) -> str | None:
    if sec is None:
        return None
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _assist_eq(col, pid: int | None):
    return col.is_(None) if pid is None else col == pid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--league-slug",
        default="bowl-cap",
        help="League config slug (default: bowl-cap). Must exist in app.config.LEAGUES.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional path to boxscore_period_scoring_summary.csv (default: league RAW_IMPORT_DIR).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report rows that would change without committing.",
    )
    args = parser.parse_args()

    if league_by_slug(args.league_slug) is None:
        print(f"Unknown league slug {args.league_slug!r}.", file=sys.stderr)
        return 1

    app = create_app(make_league_config(args.league_slug))
    with app.app_context():
        raw_dir = Path(app.config["RAW_IMPORT_DIR"])
        csv_path = args.csv or (raw_dir / "boxscore_period_scoring_summary.csv")
        if not csv_path.is_file():
            print(f"CSV not found: {csv_path}", file=sys.stderr)
            return 1

        games_map: dict[str, int] = {}
        for g in db.session.scalars(select(Game).where(Game.fhm_game_id.is_not(None))):
            if g.fhm_game_id:
                games_map[str(g.fhm_game_id).strip()] = g.id

        players_map: dict[str, int] = {}
        for p in db.session.scalars(select(Player).where(Player.fhm_player_id.is_not(None))):
            if p.fhm_player_id:
                players_map[str(p.fhm_player_id).strip()] = p.id

        teams_map: dict[int, int] = {}
        for t in db.session.scalars(select(Team).where(Team.fhm_team_id.is_not(None))):
            if t.fhm_team_id and str(t.fhm_team_id).strip().isdigit():
                teams_map[int(str(t.fhm_team_id).strip())] = t.id

        df = read_csv_normalized(csv_path)
        examined = 0
        updated = 0
        skipped_no_game = 0
        skipped_no_match = 0
        ambiguous = 0

        for _, row in df.iterrows():
            r = row.to_dict()
            gid = cell_val(r, "game_id", "gameid")
            if not gid or gid not in games_map:
                skipped_no_game += 1
                continue

            want_period = fhm_scoring_period_to_int(cell_val(r, "period"), 1)
            tsec = to_int(cell_val(r, "time"))
            time_str = _fmt_clock_seconds(tsec)
            scorer_fhm = to_int(cell_val(r, "scorer"))
            a1_fhm = to_int(cell_val(r, "assist_1"))
            a2_fhm = to_int(cell_val(r, "assist_2"))
            tm_fhm = to_int(cell_val(r, "teamid"))
            note = cell_val(r, "note")

            scorer_id = players_map.get(str(scorer_fhm)) if scorer_fhm is not None else None
            a1_id = players_map.get(str(a1_fhm)) if a1_fhm is not None else None
            a2_id = players_map.get(str(a2_fhm)) if a2_fhm is not None else None
            team_id = teams_map.get(tm_fhm) if tm_fhm is not None else None

            game_id = games_map[gid]
            stmt = (
                select(ScoringEvent)
                .where(
                    ScoringEvent.game_id == game_id,
                    ScoringEvent.scorer_player_id == scorer_id,
                    ScoringEvent.time_elapsed == time_str,
                    _assist_eq(ScoringEvent.assist1_player_id, a1_id),
                    _assist_eq(ScoringEvent.assist2_player_id, a2_id),
                )
                .order_by(ScoringEvent.id)
            )
            if team_id is not None:
                stmt = stmt.where(ScoringEvent.scoring_team_id == team_id)
            if note:
                stmt = stmt.where(ScoringEvent.strength == note)

            matches = list(db.session.scalars(stmt.limit(3)).all())
            if len(matches) != 1:
                if len(matches) == 0:
                    skipped_no_match += 1
                else:
                    ambiguous += 1
                continue

            ev = matches[0]
            examined += 1
            if int(ev.period) == want_period:
                continue
            updated += 1
            if args.dry_run:
                print(
                    f"would update scoring_events.id={ev.id} game fhm={gid!r} "
                    f"period {ev.period} -> {want_period} scorer_db={ev.scorer_player_id} time={time_str!r}"
                )
            else:
                ev.period = want_period

            if not args.dry_run and updated % 400 == 0:
                db.session.commit()

        if not args.dry_run and updated:
            db.session.commit()

        mode = "dry-run" if args.dry_run else "applied"
        print(
            f"[{mode}] csv={csv_path} examined={examined} updated={updated} "
            f"skipped_no_game={skipped_no_game} skipped_no_match={skipped_no_match} ambiguous={ambiguous}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
