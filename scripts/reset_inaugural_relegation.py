"""Reset BOWL-Relegation (bowl-fantasy) for an inaugural BLUP/BLOW season.

- Drops non-current ``seasons`` rows and their games, standings, and season stats
- Clears league history tables (awards, all-stars, champions, HOF, transactions)
- Removes WAR rows for years before the current season start year
- Clears GM achievement unlocks for this league (site membership DB)
- Resets ``history_awards.csv`` / ``history_all_stars.csv`` under raw imports
- Deletes ``instance/league_json_cache/bowl-fantasy*`` files

Does not touch other league SQLite databases (Historical, Cap, etc.).

Usage::

    python scripts/reset_inaugural_relegation.py
    python scripts/reset_inaugural_relegation.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.config import (  # noqa: E402
    BASE_DIR,
    league_raw_import_dir,
    resolve_league_sqlite_path,
    resolve_site_sqlite_path,
)

LEAGUE_SLUG = "bowl-fantasy"

GAME_CHILD_TABLES = (
    "game_skater_stats",
    "game_goalie_stats",
    "scoring_events",
    "penalty_events",
)

SEASON_CHILD_TABLES = (
    "team_standings",
    "team_season_aggregates",
    "player_skater_stats",
    "player_goalie_stats",
)

HISTORY_TABLES = (
    "history_champions",
    "history_awards",
    "history_all_stars",
    "hall_of_fame_members",
)

HONORS_TABLES = (
    "team_victory_banners",
    "team_retired_numbers",
    "team_honors_meta",
)


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _delete_where_in(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    subquery: str,
    *,
    dry_run: bool,
) -> int:
    if not _table_exists(cur, table):
        return 0
    n = cur.execute(
        f"SELECT COUNT(*) FROM [{table}] WHERE [{column}] IN ({subquery})"
    ).fetchone()[0]
    if not dry_run and n:
        cur.execute(f"DELETE FROM [{table}] WHERE [{column}] IN ({subquery})")
    return int(n)


def _delete_all(cur: sqlite3.Cursor, table: str, *, dry_run: bool) -> int:
    if not _table_exists(cur, table):
        return 0
    n = cur.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    if not dry_run and n:
        cur.execute(f"DELETE FROM [{table}]")
    return int(n)


def reset_league_db(db_path: Path, *, dry_run: bool) -> dict[str, int | str]:
    stats: dict[str, int | str] = {}
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")

        current = cur.execute(
            "SELECT id, start_year FROM seasons WHERE is_current=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not current:
            stats["error"] = "no current season row"
            return stats
        current_id, current_start_year = int(current[0]), current[1]
        stats["current_season_id"] = current_id

        old_season_ids = [
            int(r[0])
            for r in cur.execute(
                "SELECT id FROM seasons WHERE id != ? ORDER BY id",
                (current_id,),
            ).fetchall()
        ]
        stats["old_season_ids"] = ",".join(str(x) for x in old_season_ids) or "none"

        if old_season_ids:
            id_list = ",".join(str(x) for x in old_season_ids)
            game_sub = f"SELECT id FROM games WHERE season_id IN ({id_list})"

            for table in GAME_CHILD_TABLES:
                key = f"del_{table}_old_season_games"
                stats[key] = _delete_where_in(cur, table, "game_id", game_sub, dry_run=dry_run)

            if _table_exists(cur, "game_record_baselines"):
                stats["del_game_record_baselines_old_games"] = _delete_where_in(
                    cur, "game_record_baselines", "game_id", game_sub, dry_run=dry_run
                )

            stats["del_games_old_seasons"] = _delete_where_in(
                cur, "games", "season_id", id_list, dry_run=dry_run
            )

            for table in SEASON_CHILD_TABLES:
                key = f"del_{table}_old_seasons"
                stats[key] = _delete_where_in(
                    cur, table, "season_id", id_list, dry_run=dry_run
                )

            for sid in old_season_ids:
                for table in HISTORY_TABLES:
                    if not _table_exists(cur, table):
                        continue
                    cols = {
                        r[1]
                        for r in cur.execute(f"PRAGMA table_info([{table}])")
                    }
                    if "season_id" not in cols:
                        continue
                    key = f"del_{table}_season_{sid}"
                    n = cur.execute(
                        f"SELECT COUNT(*) FROM [{table}] WHERE season_id=?",
                        (sid,),
                    ).fetchone()[0]
                    if not dry_run and n:
                        cur.execute(
                            f"DELETE FROM [{table}] WHERE season_id=?",
                            (sid,),
                        )
                    stats[key] = int(n)

            if not dry_run:
                cur.execute(f"DELETE FROM seasons WHERE id IN ({id_list})")
            stats["del_season_rows"] = len(old_season_ids)

        for table in HISTORY_TABLES:
            stats[f"clear_{table}"] = _delete_all(cur, table, dry_run=dry_run)

        for table in HONORS_TABLES:
            stats[f"clear_{table}"] = _delete_all(cur, table, dry_run=dry_run)

        stats["clear_league_transactions"] = _delete_all(
            cur, "league_transactions", dry_run=dry_run
        )

        if _table_exists(cur, "player_season_war") and current_start_year is not None:
            n = cur.execute(
                "SELECT COUNT(*) FROM player_season_war WHERE season_year < ?",
                (int(current_start_year),),
            ).fetchone()[0]
            if not dry_run and n:
                cur.execute(
                    "DELETE FROM player_season_war WHERE season_year < ?",
                    (int(current_start_year),),
                )
            stats["del_player_season_war_before_current"] = int(n)

        stats["clear_record_leader_snapshots"] = _delete_all(
            cur, "record_leader_snapshots", dry_run=dry_run
        )
        stats["clear_homepage_dashboard_snapshots"] = _delete_all(
            cur, "homepage_dashboard_snapshots", dry_run=dry_run
        )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return stats


def reset_site_gm_achievements(*, dry_run: bool) -> dict[str, int]:
    out = {"gm_achievement_unlocks": 0, "gm_achievement_watermarks": 0}
    path = resolve_site_sqlite_path()
    if not path.is_file():
        return out
    conn = sqlite3.connect(str(path), timeout=60.0)
    try:
        cur = conn.cursor()
        if _table_exists(cur, "gm_achievement_unlocks"):
            n = cur.execute(
                "SELECT COUNT(*) FROM gm_achievement_unlocks WHERE league_slug=?",
                (LEAGUE_SLUG,),
            ).fetchone()[0]
            if not dry_run and n:
                cur.execute(
                    "DELETE FROM gm_achievement_unlocks WHERE league_slug=?",
                    (LEAGUE_SLUG,),
                )
            out["gm_achievement_unlocks"] = int(n)
        if _table_exists(cur, "gm_achievement_watermarks"):
            n = cur.execute(
                "SELECT COUNT(*) FROM gm_achievement_watermarks WHERE league_slug=?",
                (LEAGUE_SLUG,),
            ).fetchone()[0]
            if not dry_run and n:
                cur.execute(
                    "DELETE FROM gm_achievement_watermarks WHERE league_slug=?",
                    (LEAGUE_SLUG,),
                )
            out["gm_achievement_watermarks"] = int(n)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return out


def reset_history_csvs(raw_dir: Path, *, dry_run: bool) -> list[str]:
    touched: list[str] = []
    specs = {
        "history_awards.csv": "season,award_name,player_id,team_id,staff_id,notes\n",
        "history_all_stars.csv": "season,team,slot,position,player_id,team_id,notes\n",
        "hall_of_fame.csv": "fhm_player_id,kind,inducted_year,sort_order\n",
        "transactions.csv": "date;type;team;other_team;player;player_id;headline;body;external_id\n",
    }
    for name, header in specs.items():
        path = raw_dir / name
        if not path.is_file() and name != "history_champions.csv":
            continue
        rel = str(path.relative_to(BASE_DIR)).replace("\\", "/")
        if dry_run:
            touched.append(f"would rewrite {rel}")
            continue
        path.write_text(header, encoding="utf-8", newline="\n")
        touched.append(rel)
    hc = raw_dir / "history_champions.csv"
    if hc.is_file():
        rel = str(hc.relative_to(BASE_DIR)).replace("\\", "/")
        if dry_run:
            touched.append(f"would delete {rel}")
        else:
            hc.unlink(missing_ok=True)
            touched.append(f"deleted {rel}")
    return touched


def clear_json_cache(*, dry_run: bool) -> list[str]:
    cache_dir = BASE_DIR / "instance" / "league_json_cache"
    removed: list[str] = []
    if not cache_dir.is_dir():
        return removed
    for path in sorted(cache_dir.glob(f"{LEAGUE_SLUG}__*")):
        rel = str(path.relative_to(BASE_DIR)).replace("\\", "/")
        if dry_run:
            removed.append(f"would delete {rel}")
        else:
            path.unlink(missing_ok=True)
            removed.append(rel)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not modify databases or files.",
    )
    args = parser.parse_args()

    db_path = resolve_league_sqlite_path(LEAGUE_SLUG)
    if not db_path.is_file():
        print(f"No database at {db_path}", file=sys.stderr)
        return 1

    raw_dir = BASE_DIR / "data" / "imports" / "raw" / league_raw_import_dir(LEAGUE_SLUG)

    print(f"League: {LEAGUE_SLUG}")
    print(f"Database: {db_path}")
    print(f"Raw import dir: {raw_dir}")
    if args.dry_run:
        print("(dry run — no writes)")

    db_stats = reset_league_db(db_path, dry_run=args.dry_run)
    if "error" in db_stats:
        print("ERROR:", db_stats["error"], file=sys.stderr)
        return 1
    print("\nLeague DB:")
    for k, v in sorted(db_stats.items()):
        if k in ("current_season_id", "old_season_ids", "error") or (isinstance(v, int) and v > 0):
            print(f"  {k}: {v}")

    gm_stats = reset_site_gm_achievements(dry_run=args.dry_run)
    print("\nSite membership DB:")
    for k, v in gm_stats.items():
        if v:
            print(f"  {k}: {v}")

    csv_touched = reset_history_csvs(raw_dir, dry_run=args.dry_run)
    if csv_touched:
        print("\nImport CSVs:")
        for line in csv_touched:
            print(f"  {line}")

    cache_removed = clear_json_cache(dry_run=args.dry_run)
    if cache_removed:
        print("\nJSON cache:")
        for line in cache_removed:
            print(f"  {line}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
