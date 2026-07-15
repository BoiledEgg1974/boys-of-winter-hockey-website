"""Remove team-page honors panel images and clear DB image path columns.

Deletes PNG/JPEG/WebP/GIF files under ``app/static/team_honors/`` (retired
jerseys and victory banners) for all leagues. Does **not** touch champions
history banners under ``app/static/img/history/champions/``.

Also nulls ``jersey_image_rel_path`` / ``banner_image_rel_path`` on each league
SQLite database so recreated honors start with a clean media slate.

Examples::

    python scripts/cleanup_team_honors_media.py
    python scripts/cleanup_team_honors_media.py --dry-run
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

from app.config import BASE_DIR, league_slugs, resolve_league_sqlite_path  # noqa: E402

TEAM_HONORS_DIR = BASE_DIR / "app" / "static" / "team_honors"
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def delete_team_honors_images(*, dry_run: bool = False) -> dict:
    deleted: list[str] = []
    if not TEAM_HONORS_DIR.is_dir():
        return {"ok": True, "deleted": deleted, "missing": True}

    for path in sorted(TEAM_HONORS_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        deleted.append(str(path.relative_to(BASE_DIR)).replace("\\", "/"))
        if not dry_run:
            path.unlink(missing_ok=True)

    if not dry_run:
        for parent in (TEAM_HONORS_DIR / "banners", TEAM_HONORS_DIR / "retired_numbers"):
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir(), reverse=True):
                if child.is_dir() and not any(child.rglob("*")):
                    child.rmdir()

    return {"ok": True, "deleted": deleted, "missing": False}


def clear_league_image_paths(*, dry_run: bool = False) -> list[dict]:
    results: list[dict] = []
    for slug in league_slugs():
        db_path = resolve_league_sqlite_path(slug)
        info: dict = {"slug": slug, "path": str(db_path), "ok": False}
        if not db_path.is_file():
            info["message"] = "database not found"
            results.append(info)
            continue
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                cleared: dict[str, int] = {}
                if "team_retired_numbers" in tables:
                    before = conn.execute(
                        "SELECT COUNT(*) FROM team_retired_numbers "
                        "WHERE jersey_image_rel_path IS NOT NULL "
                        "AND TRIM(jersey_image_rel_path) != ''"
                    ).fetchone()[0]
                    if not dry_run and before:
                        conn.execute(
                            "UPDATE team_retired_numbers SET jersey_image_rel_path = NULL "
                            "WHERE jersey_image_rel_path IS NOT NULL "
                            "AND TRIM(jersey_image_rel_path) != ''"
                        )
                    cleared["team_retired_numbers"] = int(before)
                if "team_victory_banners" in tables:
                    before = conn.execute(
                        "SELECT COUNT(*) FROM team_victory_banners "
                        "WHERE banner_image_rel_path IS NOT NULL "
                        "AND TRIM(banner_image_rel_path) != ''"
                    ).fetchone()[0]
                    if not dry_run and before:
                        conn.execute(
                            "UPDATE team_victory_banners SET banner_image_rel_path = NULL "
                            "WHERE banner_image_rel_path IS NOT NULL "
                            "AND TRIM(banner_image_rel_path) != ''"
                        )
                    cleared["team_victory_banners"] = int(before)
                if not dry_run:
                    conn.commit()
                info["ok"] = True
                info["cleared"] = cleared
            finally:
                conn.close()
        except sqlite3.Error as exc:
            info["message"] = str(exc)
        results.append(info)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted/cleared without writing.",
    )
    args = parser.parse_args()

    media = delete_team_honors_images(dry_run=args.dry_run)
    db_rows = clear_league_image_paths(dry_run=args.dry_run)

    prefix = "Would delete" if args.dry_run else "Deleted"
    print(f"{prefix} {len(media.get('deleted') or [])} team honors image(s).")
    for rel in media.get("deleted") or []:
        print(f"  {rel}")

    for row in db_rows:
        slug = row.get("slug")
        if not row.get("ok"):
            print(f"  {slug}: SKIP — {row.get('message')}")
            continue
        cleared = row.get("cleared") or {}
        total = sum(int(v) for v in cleared.values())
        verb = "Would clear" if args.dry_run else "Cleared"
        print(f"  {slug}: {verb} {total} image path(s) {cleared}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
