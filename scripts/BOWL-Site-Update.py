#!/usr/bin/env python3
"""BOWL-Site-Update: one-command local + PythonAnywhere update pipeline.

You can run the same flow via ``python scripts/run_site_update.py bowl`` so it sits next to
``to-live`` / ``local`` / ``deploy`` in one entry point.

Default flow:
1) Run STEP1 (snapshot OVR baselines, copy saved-game CSVs, local imports) with PA deploy and git push skipped.
2) Align historical awards IDs to player_master (STEP3).
3) Snapshot bowl-historical OVR baselines, then re-import that league locally (aligned awards).
4) Commit and push to GitHub once all local imports finish (CSVs, static assets, alignment files).
5) Run STEP2 ``deploy-db``: snapshot live OVR on PythonAnywhere, merge those baselines into the
   local SQLite files, upload league databases (+ ``app/static``), integrity-check, reload.

Use ``--remote-import`` to use the older CSV + server-side ``import_data.py`` deploy instead.

Examples:
  python scripts/BOWL-Site-Update.py
  python scripts/run_site_update.py bowl
  python scripts/BOWL-Site-Update.py --mode fullremoterebuild
  python scripts/BOWL-Site-Update.py --allow-stale
  python scripts/BOWL-Site-Update.py --no-deploy
  python scripts/BOWL-Site-Update.py --no-push
  python scripts/BOWL-Site-Update.py --deploy-db-only
  python scripts/BOWL-Site-Update.py --remote-import

Use ``flask bowl-overall-baseline-refresh`` only to treat the current site as a fresh baseline
(clears trend arrows until the next pre-import snapshot).
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
REPAIR = REPO_ROOT / "scripts" / "repair_league_sqlite.py"
HISTORY_SHEET_EXTRAS = REPO_ROOT / "scripts" / "reimport_history_sheet_data.py"

HIST_RAW = REPO_ROOT / "data" / "imports" / "raw" / "bowl_historical"
HIST_AWARDS_SHEET = HIST_RAW / "history_awards.sheet.csv"

LEAGUE_SLUGS = ("bowl-historical", "bowl-fantasy", "bowl-cap")

# Default PythonAnywhere deploy key (override with PA_SSH_KEY in the environment).
_DEFAULT_PA_SSH_KEY = Path.home() / ".ssh" / "id_ed25519_pa"


def _pa_deploy_env() -> dict[str, str]:
    """Environment for STEP2 deploy/deploy-db; sets PA_SSH_KEY when unset."""
    env = dict(os.environ)
    if not (env.get("PA_SSH_KEY") or "").strip():
        if _DEFAULT_PA_SSH_KEY.is_file():
            env["PA_SSH_KEY"] = str(_DEFAULT_PA_SSH_KEY)
            print(f"Using SSH key: {_DEFAULT_PA_SSH_KEY}")
        else:
            print(
                f"Warning: PA_SSH_KEY is not set and {_DEFAULT_PA_SSH_KEY} was not found.",
                file=sys.stderr,
            )
    return env


def _deploy_preflight_note() -> None:
    print(
        "\nDeploy preflight: reload the PythonAnywhere web app (Web tab -> Reload) "
        "before upload so no worker is writing to SQLite.\n"
    )

def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True)


def _git_changes_present() -> bool:
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(res.stdout.strip())


def _commit_and_push_local_changes() -> None:
    """Commit tracked import/alignment changes after every local import step has finished."""
    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
    if not _git_changes_present():
        print("No git changes to commit after local imports (SQLite DB files are gitignored).")
        return
    msg = (
        "Update league CSV imports and local alignment files\n\n"
        f"Automated BOWL-Site-Update local run at {datetime.now().isoformat(timespec='seconds')}."
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Git push complete.")


def _verify_local_league_databases() -> None:
    print("Verifying local league SQLite files before deploy-db...")
    for slug in LEAGUE_SLUGS:
        _run([sys.executable, str(REPAIR), "--check", "--league", slug])


def main() -> int:
    ap = argparse.ArgumentParser(description="One-command BOWL website update and deploy pipeline.")
    ap.add_argument(
        "--mode",
        choices=("regular", "fullremoterebuild"),
        default="regular",
        help="regular (default) or fullremoterebuild recovery mode on PythonAnywhere.",
    )
    ap.add_argument("--allow-stale", action="store_true", help="Pass through to STEP1 stale-source override.")
    ap.add_argument(
        "--no-push",
        action="store_true",
        help="Skip git commit/push after local imports (default: commit and push once imports finish).",
    )
    ap.add_argument("--no-deploy", action="store_true", help="Skip PythonAnywhere deploy step.")
    ap.add_argument(
        "--deploy-db-only",
        action="store_true",
        help="Skip local imports and git push; only verify local DBs and run deploy-db.",
    )
    ap.add_argument(
        "--remote-import",
        action="store_true",
        help="Deploy via CSV upload + server-side import_data.py instead of uploading SQLite files.",
    )
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

    if args.deploy_db_only and args.no_deploy:
        print("Use either --deploy-db-only or --no-deploy, not both.", file=sys.stderr)
        return 1

    if args.deploy_db_only:
        print("BOWL-Site-Update (deploy-db only)...")
        _deploy_preflight_note()
        _verify_local_league_databases()
        step2_cmd = [sys.executable, str(STEP2), "deploy-db"]
        if args.sync_ap_catalog_local:
            step2_cmd.append("--sync-ap-catalog-local")
        _run(step2_cmd, env=_pa_deploy_env())
        print("\nBOWL-Site-Update complete.")
        return 0

    print("BOWL-Site-Update starting...")

    # 1) STEP1: copy CSVs + local imports; defer git push until after the historical re-import below.
    step1_cmd = [sys.executable, str(STEP1), "--no-pa-deploy", "--no-push"]
    if args.allow_stale:
        step1_cmd.append("--allow-stale")
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
    snap = REPO_ROOT / "scripts" / "snapshot_ovr_baseline.py"
    _run([sys.executable, str(snap)], env=env)
    _run([sys.executable, str(IMPORT)], env=env)
    if HISTORY_SHEET_EXTRAS.is_file():
        _run([sys.executable, str(HISTORY_SHEET_EXTRAS), "bowl-historical"], env=env)

    # 4) Commit + push once all local imports are done (before PythonAnywhere deploy).
    if not args.no_push:
        try:
            _commit_and_push_local_changes()
        except subprocess.CalledProcessError as exc:
            print(f"Git commit/push failed: {exc}", file=sys.stderr)
            return int(exc.returncode or 1)
    else:
        print("Skipping git commit/push (--no-push).")

    # 5) Deploy to PythonAnywhere.
    if not args.no_deploy:
        _deploy_preflight_note()
        _verify_local_league_databases()
        deploy_env = _pa_deploy_env()
        if args.remote_import:
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
            _run(step2_cmd, env=deploy_env)
        else:
            step2_cmd = [sys.executable, str(STEP2), "deploy-db"]
            if args.sync_ap_catalog_local:
                step2_cmd.append("--sync-ap-catalog-local")
            _run(step2_cmd, env=deploy_env)
    else:
        print("Skipping PythonAnywhere deploy (--no-deploy).")

    print("\nBOWL-Site-Update complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
