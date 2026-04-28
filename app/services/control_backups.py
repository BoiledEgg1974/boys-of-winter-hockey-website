from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from app.config import BASE_DIR, resolve_league_sqlite_path


def _backup_dir_for_slug(league_slug: str) -> Path:
    return (BASE_DIR / "instance" / "league_backups" / league_slug).resolve()


def create_league_backup(league_slug: str, reason: str) -> dict:
    src = resolve_league_sqlite_path(league_slug)
    if not src.is_file():
        return {"ok": False, "message": f"League DB not found: {src}", "path": ""}
    out_dir = _backup_dir_for_slug(league_slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_reason = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (reason or "op"))[:40]
    out = out_dir / f"{ts}_{safe_reason}.db"
    shutil.copy2(src, out)
    return {"ok": True, "message": "Backup created.", "path": str(out)}


def list_league_backups(league_slug: str, limit: int = 20) -> list[dict]:
    out_dir = _backup_dir_for_slug(league_slug)
    if not out_dir.is_dir():
        return []
    files = sorted(out_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, limit)]
    rows = []
    for p in files:
        st = p.stat()
        rows.append(
            {
                "name": p.name,
                "path": str(p),
                "size_bytes": int(st.st_size),
                "mtime_utc": datetime.utcfromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return rows


def restore_league_backup(league_slug: str, backup_name: str) -> dict:
    target_name = Path(str(backup_name or "")).name
    if not target_name:
        return {"ok": False, "message": "Invalid backup name."}
    backup_path = _backup_dir_for_slug(league_slug) / target_name
    if not backup_path.is_file():
        return {"ok": False, "message": f"Backup file not found: {backup_path}"}
    dst = resolve_league_sqlite_path(league_slug)
    if not dst.parent.is_dir():
        dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, dst)
    return {"ok": True, "message": "Backup restored.", "path": str(backup_path), "restored_to": str(dst)}
