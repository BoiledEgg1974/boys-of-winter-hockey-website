#!/usr/bin/env python3
"""BOWL-Site-Update: one-command local + PythonAnywhere update pipeline.

Default flow:
1) Run STEP1 (copy saved-game CSVs + local imports + git push) with PA deploy skipped.
2) Align historical awards IDs to player_master (STEP3).
3) Re-import historical league locally (to apply aligned awards immediately).
4) Refresh player OVR baseline rows on each local league DB (trend arrows on the site).
5) Commit/push any STEP3-generated file changes (if present).
6) Run STEP2 deploy to PythonAnywhere using repo CSV folders.

Examples:
  python scripts/BOWL-Site-Update.py
  python scripts/BOWL-Site-Update.py --mode fullremoterebuild
  python scripts/BOWL-Site-Update.py --allow-stale
  python scripts/BOWL-Site-Update.py --no-deploy
  python scripts/BOWL-Site-Update.py --no-push

After imports, each league's local SQLite DB is updated with ``player_overall_baselines`` so OVR
trend arrows reset on the site. On PythonAnywhere, run ``flask bowl-overall-baseline-refresh`` per
league after the server-side import if you want the same there.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STEP1 = REPO_ROOT / "scripts" / "STEP1_update_from_saved_game.py"
STEP2 = REPO_ROOT / "scripts" / "STEP2_pythonanywhere.py"
STEP3 = REPO_ROOT / "scripts" / "STEP3_align_history_awards_to_player_master.py"
IMPORT = REPO_ROOT / "scripts" / "import_data.py"

HIST_RAW = REPO_ROOT / "data" / "imports" / "raw" / "bowl_historical"
HIST_AWARDS_SHEET = HIST_RAW / "history_awards.sheet.csv"


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True)


def _refresh_ovr_baselines_all_leagues() -> None:
    """Persist current 1-100 OVR for every player on each local league SQLite DB."""
    rp = str(REPO_ROOT)
    if rp not in sys.path:
        sys.path.insert(0, rp)
    from app import create_app
    from app.config import league_slugs, make_league_config
    from app.models import db
    from app.services.player_overall_score import refresh_all_player_overall_baselines

    print("\n>>> OVR baseline refresh (local league databases)")
    for slug in league_slugs():
        app = create_app(make_league_config(slug))
        with app.app_context():
            n = refresh_all_player_overall_baselines(db.session)
        print(f"    {slug}: {n} players")


def _git_changes_present() -> bool:
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(res.stdout.strip())


def _commit_and_push_step3_changes() -> None:
    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
    if not _git_changes_present():
        print("No additional STEP3 changes to commit.")
        return
    msg = (
        "Align historical awards IDs to player master.\n\n"
        f"Automated BOWL-Site-Update run at {datetime.now().isoformat(timespec='seconds')}."
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Pushed STEP3 alignment changes.")


def main() -> int:
    ap = argparse.ArgumentParser(description="One-command BOWL website update and deploy pipeline.")
    ap.add_argument(
        "--mode",
        choices=("regular", "fullremoterebuild"),
        default="regular",
        help="regular (default) or fullremoterebuild recovery mode on PythonAnywhere.",
    )
    ap.add_argument("--allow-stale", action="store_true", help="Pass through to STEP1 stale-source override.")
    ap.add_argument("--no-push", action="store_true", help="Skip all git commit/push actions.")
    ap.add_argument("--no-deploy", action="store_true", help="Skip PythonAnywhere deploy step.")
    ap.add_argument(
        "--remote-pip",
        action="store_true",
        help="During STEP2 deploy, run remote pip install -r requirements.txt before imports.",
    )
    ap.add_argument(
        "--sync-ap-catalog-local",
        action="store_true",
        help="During STEP2 deploy, sync live AP catalog back into local DB for verification.",
    )
    args = ap.parse_args()

    if not STEP1.is_file() or not STEP2.is_file() or not STEP3.is_file():
        print("Missing one or more required scripts (STEP1/STEP2/STEP3).", file=sys.stderr)
        return 1

    print("BOWL-Site-Update starting...")

    # 1) STEP1: copy CSVs + local imports + optional git push, but skip its PA deploy.
    step1_cmd = [sys.executable, str(STEP1), "--no-pa-deploy"]
    if args.allow_stale:
        step1_cmd.append("--allow-stale")
    if args.no_push:
        step1_cmd.append("--no-push")
    else:
        step1_cmd.append("--yes-push")
    _run(step1_cmd)

    # 2) STEP3: align historical awards IDs.
    step3_cmd = [
        sys.executable,
        str(STEP3),
        "--raw-dir",
        str(HIST_RAW),
        "--output",
        str(HIST_AWARDS_SHEET),
    ]
    _run(step3_cmd)

    # 3) Re-import historical locally so aligned awards are applied immediately.
    env = dict(os.environ)
    env["LEAGUE_SLUG"] = "bowl-historical"
    _run([sys.executable, str(IMPORT)], env=env)

    # 4) OVR trend baselines: align site arrows with freshly imported ratings (all leagues).
    _refresh_ovr_baselines_all_leagues()

    # 5) Commit + push any new STEP3-alignment changes if enabled.
    if not args.no_push:
        _commit_and_push_step3_changes()
    else:
        print("Skipping STEP3 git push (--no-push).")

    # 6) Deploy from repo CSV folders to PythonAnywhere.
    if not args.no_deploy:
        step2_cmd = [sys.executable, str(STEP2), "deploy", "--repo-csv"]
        if args.mode == "fullremoterebuild":
            if sys.stdin.isatty():
                confirm = input(
                    "Full remote rebuild mode will hard-reset remote git and recreate remote venv. Continue? [y/N]: "
                ).strip().lower()
                if confirm not in {"y", "yes"}:
                    print("Cancelled full remote rebuild mode.")
                    return 1
            step2_cmd.append("--full-remote-rebuild")
        if args.remote_pip:
            step2_cmd.append("--remote-pip")
        if args.sync_ap_catalog_local:
            step2_cmd.append("--sync-ap-catalog-local")
        _run(step2_cmd)
    else:
        print("Skipping PythonAnywhere deploy (--no-deploy).")

    print("\nBOWL-Site-Update complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

