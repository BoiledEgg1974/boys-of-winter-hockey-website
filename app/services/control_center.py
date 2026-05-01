from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.models import Game, ImportLog, Player, Season, Team, TeamStanding
from app.services.seasons import season_display_label


def _iso_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def build_control_center_snapshot(session, raw_dir: Path) -> dict:
    current_season = session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current_season is None:
        current_season = session.scalar(select(Season).order_by(Season.id.desc()).limit(1))

    season_id = int(current_season.id) if current_season else None
    season_label = season_display_label(current_season) if current_season else "—"

    teams_total = int(session.scalar(select(func.count(Team.id))) or 0)
    players_total = int(session.scalar(select(func.count(Player.id))) or 0)
    games_total = 0
    finals_total = 0
    standings_total = 0
    latest_game_date = None
    if season_id is not None:
        games_total = int(session.scalar(select(func.count(Game.id)).where(Game.season_id == season_id)) or 0)
        finals_total = int(
            session.scalar(
                select(func.count(Game.id)).where(Game.season_id == season_id, Game.status == "final")
            )
            or 0
        )
        standings_total = int(
            session.scalar(select(func.count(TeamStanding.id)).where(TeamStanding.season_id == season_id))
            or 0
        )
        latest_game_date = session.scalar(select(func.max(Game.game_date)).where(Game.season_id == season_id))

    latest_import = session.scalar(select(ImportLog).order_by(ImportLog.id.desc()).limit(1))

    csv_total = 0
    csv_newest = None
    csv_names: list[str] = []
    if raw_dir.is_dir():
        files = sorted(raw_dir.glob("*.csv"))
        csv_total = len(files)
        csv_names = [f.name for f in files[:25]]
        if files:
            newest_ts = max(f.stat().st_mtime for f in files)
            csv_newest = datetime.fromtimestamp(newest_ts)

    return {
        "current_season": {"id": season_id, "label": season_label},
        "counts": {
            "teams_total": teams_total,
            "players_total": players_total,
            "games_total": games_total,
            "finals_total": finals_total,
            "standings_rows": standings_total,
        },
        "latest_game_date": latest_game_date.isoformat() if latest_game_date else None,
        "latest_import": {
            "file_name": str(latest_import.file_name) if latest_import else "",
            "status": str(latest_import.status) if latest_import else "",
            "rows_processed": int(latest_import.rows_processed or 0) if latest_import else 0,
            "started_at": _iso_dt(latest_import.started_at) if latest_import else None,
            "finished_at": _iso_dt(latest_import.finished_at) if latest_import else None,
            "message": str(latest_import.message or "") if latest_import else "",
        },
        "raw_folder": {
            "path": str(raw_dir),
            "csv_total": csv_total,
            "newest_csv_mtime": _iso_dt(csv_newest),
            "sample_files": csv_names,
        },
    }


def dry_run_operation_plan(*, repo_root: Path, league_slug: str, operation: str) -> dict:
    op = str(operation or "").strip().lower()
    script_import = repo_root / "scripts" / "import_data.py"
    script_refresh = repo_root / "scripts" / "refresh_team_aggregates.py"
    script_step1 = repo_root / "scripts" / "STEP1_update_from_saved_game.py"
    steps: list[str] = []
    if op == "import_refresh":
        steps = [
            f"Set LEAGUE_SLUG={league_slug}",
            f"Run: python {script_import}",
            f"Run: python {script_refresh}",
            "Verify latest import_logs row status is success",
            "Verify standings/homepage endpoints load without errors",
        ]
    elif op == "step1_local":
        steps = [
            f"Run local updater interactively: python {script_step1}",
            "Copy league CSV exports into data/imports/raw/*",
            "Run per-league import pipeline (inside STEP1)",
            "Optional git commit/push prompt (inside STEP1)",
            "Optional PythonAnywhere deploy prompt (inside STEP1)",
        ]
    else:
        return {"operation": op, "ok": False, "steps": [], "message": "Unknown dry-run operation."}
    return {
        "operation": op,
        "ok": True,
        "steps": steps,
        "message": "Dry run preview only. No commands executed.",
        "paths": {
            "repo_root": str(repo_root),
            "import_script": str(script_import),
            "refresh_script": str(script_refresh),
            "step1_script": str(script_step1),
        },
    }
