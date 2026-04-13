"""STEP 1 — Interactive updater: copy CSV exports, import all leagues, optionally git push.

Optional: after copying, push to PythonAnywhere with ``scripts/STEP2_pythonanywhere.py deploy``.
That upload only replaces remote CSVs when the local copy is newer (mtime check + small
skew), then runs server-side imports and reloads WSGI. Local copy uses ``shutil.copy2``,
so mtimes match your game export folders.

Run directly:
    python scripts/STEP1_update_from_saved_game.py
    python scripts/STEP1_update_from_saved_game.py --pa-deploy
    python scripts/STEP1_update_from_saved_game.py --pa-deploy --pa-csv-only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = REPO_ROOT / "data" / "imports" / "raw"
IMPORT_SCRIPT = REPO_ROOT / "scripts" / "import_data.py"
PA_DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "STEP2_pythonanywhere.py"
PATHS_FILE = REPO_ROOT / "scripts" / "saved_game_csv_paths.json"

DEFAULT_SOURCES: dict[str, str] = {
    "bowl-historical": r"C:\Users\keeno\OneDrive\Documents\Out of the Park Developments\Franchise Hockey Manager 11\saved_games\BOWL-Historical.lg\import_export\csv",
    "bowl-fantasy": r"C:\Users\keeno\OneDrive\Documents\Out of the Park Developments\Franchise Hockey Manager 11\saved_games\BOWL-Fantasy.lg\import_export\csv",
    "bowl-cap": r"C:\Users\keeno\OneDrive\Documents\Out of the Park Developments\Franchise Hockey Manager 11\saved_games\BOWL-Soft Cap.lg\import_export\csv",
}

# Keys in saved_game_csv_paths.json from before slug rename
_LEGACY_SLUG_KEYS = {"league2": "bowl-historical", "bow": "bowl-fantasy", "league3": "bowl-cap"}


@dataclass(frozen=True)
class LeagueCopyTarget:
    label: str
    slug: str
    import_dir: str


LEAGUES: tuple[LeagueCopyTarget, ...] = (
    LeagueCopyTarget("BOWL-Historical", "bowl-historical", "bowl_historical"),
    LeagueCopyTarget("BOWL-Fantasy", "bowl-fantasy", "bowl_fantasy"),
    LeagueCopyTarget("BOWL-Cap", "bowl-cap", "bowl_cap"),
)


def _prompt_path(prompt: str, default: Path | None = None) -> Path | None:
    if default is not None:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
    else:
        raw = input(f"{prompt}: ").strip()
    if raw == "":
        return None
    if raw == "-":
        return None
    return Path(raw.strip('"')).expanduser()


def _ask_yes_no(prompt: str, *, default_no: bool = True) -> bool:
    try:
        raw = input(prompt).strip().lower()
    except EOFError:
        return not default_no
    if raw in {"y", "yes"}:
        return True
    if raw in {"n", "no", ""}:
        return False if default_no else True
    return False if default_no else True


def _load_saved_paths() -> dict[str, str]:
    if PATHS_FILE.exists():
        try:
            data = json.loads(PATHS_FILE.read_text(encoding="utf-8"))
            out = dict(DEFAULT_SOURCES)
            if isinstance(data, dict):
                for key, val in data.items():
                    if not isinstance(val, str) or not val.strip():
                        continue
                    nk = _LEGACY_SLUG_KEYS.get(key, key)
                    if nk in DEFAULT_SOURCES:
                        out[nk] = val.strip()
            return out
        except (json.JSONDecodeError, OSError):
            pass
    # No local json yet: use in-repo defaults only (do not write machine-specific paths).
    return dict(DEFAULT_SOURCES)


def _save_paths(paths: dict[str, str]) -> None:
    PATHS_FILE.write_text(json.dumps(paths, indent=2), encoding="utf-8")


def _copy_csvs(src: Path, dst: Path) -> int:
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Source folder not found: {src}")
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.csv"))
    if not files:
        return 0
    for f in files:
        shutil.copy2(f, dst / f.name)
    return len(files)


def _latest_csv_mtime(src: Path) -> datetime | None:
    if not src.exists() or not src.is_dir():
        return None
    files = list(src.glob("*.csv"))
    if not files:
        return None
    return datetime.fromtimestamp(max(f.stat().st_mtime for f in files))


def _run_import(slug: str) -> None:
    env = dict(**os.environ)
    env["LEAGUE_SLUG"] = slug
    cmd = [sys.executable, str(IMPORT_SCRIPT)]
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


def _git_commit_and_push() -> None:
    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
    if not _git_changes_present():
        print("No git changes detected after add; nothing to commit.")
        return
    msg = (
        "Update league CSV imports and rebuild databases\n\n"
        f"Automated run from saved game exports at {datetime.now().isoformat(timespec='seconds')}."
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Git push complete.")


def _run_pythonanywhere_deploy(*, csv_only: bool) -> None:
    """Upload from repo raw folders; STEP2_pythonanywhere skips remote files that are same/newer."""
    if not PA_DEPLOY_SCRIPT.is_file():
        raise FileNotFoundError(f"Missing {PA_DEPLOY_SCRIPT}")
    cmd = [
        sys.executable,
        str(PA_DEPLOY_SCRIPT),
        "deploy",
        "--repo-csv",
    ]
    if csv_only:
        cmd.append("--csv-only")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy saved-game CSVs, import leagues, and optionally push.")
    parser.add_argument("--base", type=str, default=None, help="Base export folder containing league subfolders.")
    parser.add_argument("--historical", type=str, default=None, help="Override source folder for BOWL-Historical.")
    parser.add_argument("--fantasy", type=str, default=None, help="Override source folder for BOWL-Fantasy.")
    parser.add_argument("--cap", type=str, default=None, help="Override source folder for BOWL-Cap.")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow continuing even if one league source CSVs are much older than the freshest source.",
    )
    parser.add_argument("--yes-push", action="store_true", help="Commit and push automatically (no prompt).")
    parser.add_argument("--no-push", action="store_true", help="Skip commit and push automatically (no prompt).")
    parser.add_argument(
        "--pa-deploy",
        action="store_true",
        help="After local imports, run STEP2_pythonanywhere.py deploy --repo-csv (newer files only on server).",
    )
    parser.add_argument(
        "--no-pa-deploy",
        action="store_true",
        help="Never upload to PythonAnywhere (skip prompt).",
    )
    parser.add_argument(
        "--pa-csv-only",
        action="store_true",
        help="With --pa-deploy: only upload data/imports/raw, not app/static.",
    )
    args = parser.parse_args()

    print("\nBoys of Winter: Saved-Game CSV Updater\n")

    saved_paths = _load_saved_paths()

    copied_slugs: list[str] = []
    cli_overrides = {
        "bowl-historical": Path(args.historical.strip('"')).expanduser() if args.historical else None,
        "bowl-fantasy": Path(args.fantasy.strip('"')).expanduser() if args.fantasy else None,
        "bowl-cap": Path(args.cap.strip('"')).expanduser() if args.cap else None,
    }

    # Build league source map
    league_sources: dict[str, Path | None] = {}

    if args.base:
        base = Path(args.base.strip('"')).expanduser()
        if not base.exists() or not base.is_dir():
            print(f"Base folder does not exist: {base}")
            return 1
        print(f"Using base export folder from args: {base}")
        for league in LEAGUES:
            league_sources[league.slug] = base / league.import_dir
    else:
        paths_changed = _ask_yes_no("Have your saved-game CSV paths changed since last run? [y/N]: ")
        if paths_changed:
            print("Enter new CSV source paths for each league:")
            updated_paths = dict(saved_paths)
            for league in LEAGUES:
                default_src = Path(saved_paths[league.slug])
                new_src = _prompt_path(f"{league.label} source path", default=default_src)
                if new_src is None:
                    print(f"{league.label} cannot be blank when updating paths. Exiting.")
                    return 1
                updated_paths[league.slug] = str(new_src)
            _save_paths(updated_paths)
            saved_paths = updated_paths
            print(f"Saved updated paths to: {PATHS_FILE}")
        else:
            print("Using last known source paths.")
        for league in LEAGUES:
            league_sources[league.slug] = Path(saved_paths[league.slug])

    # CLI per-league overrides always win.
    for slug, p in cli_overrides.items():
        if p is not None:
            league_sources[slug] = p

    latest_by_slug: dict[str, datetime] = {}
    for league in LEAGUES:
        src = league_sources.get(league.slug)
        if src is None:
            continue
        latest = _latest_csv_mtime(src)
        if latest is not None:
            latest_by_slug[league.slug] = latest
    stale_leagues: list[str] = []
    if latest_by_slug:
        freshest = max(latest_by_slug.values())
        stale_threshold_seconds = 18 * 60 * 60
        for league in LEAGUES:
            latest = latest_by_slug.get(league.slug)
            if latest is None:
                continue
            age_delta = (freshest - latest).total_seconds()
            if age_delta > stale_threshold_seconds:
                stale_leagues.append(league.slug)
                print(
                    f"! Warning: {league.label} source CSVs look stale "
                    f"({latest.isoformat(sep=' ', timespec='seconds')}) vs freshest league "
                    f"({freshest.isoformat(sep=' ', timespec='seconds')}). "
                    "If this is unexpected, export CSVs again in FHM before continuing."
                )
    if stale_leagues and not args.allow_stale:
        if sys.stdin.isatty():
            proceed = _ask_yes_no(
                "One or more league exports look stale. Continue anyway? [y/N]: "
            )
            if not proceed:
                print(
                    "Stopped due to stale league source CSVs. "
                    "Re-export in FHM first, or re-run with --allow-stale to override."
                )
                return 1
        else:
            print(
                "Refusing to continue with stale league source CSVs. "
                "Re-export in FHM first, or re-run with --allow-stale to override."
            )
            return 1

    for league in LEAGUES:
        src = league_sources.get(league.slug)
        if src is None:
            print(f"- Skipped {league.label}")
            continue
        print(f"{league.label}: source -> {src}")
        try:
            count = _copy_csvs(src, RAW_ROOT / league.import_dir)
        except FileNotFoundError as exc:
            print(f"! {exc}")
            continue
        print(f"- {league.label}: copied {count} CSV file(s)")
        copied_slugs.append(league.slug)

    if not copied_slugs:
        print("No league CSVs copied. Exiting.")
        return 1

    print("\nRunning imports...")
    for slug in copied_slugs:
        print(f"- Importing {slug} ...")
        _run_import(slug)
    print("Imports complete.")

    if args.yes_push and args.no_push:
        print("Use only one of --yes-push or --no-push.")
        return 1
    if args.pa_deploy and args.no_pa_deploy:
        print("Use only one of --pa-deploy or --no-pa-deploy.")
        return 1
    if args.yes_push:
        do_git = True
    elif args.no_push:
        do_git = False
    else:
        do_git = _ask_yes_no("\nCommit and push to GitHub now? [y/N]: ")
    if do_git:
        try:
            _git_commit_and_push()
        except subprocess.CalledProcessError as exc:
            print(f"Git command failed: {exc}")
            return int(exc.returncode or 1)
    else:
        print("Skipped git commit/push.")

    if args.pa_deploy:
        do_pa = True
    elif args.no_pa_deploy:
        do_pa = False
    else:
        do_pa = _ask_yes_no(
            "\nUpload to PythonAnywhere (CSV files newer than server only), "
            "run server imports, reload app? [y/N]: "
        )
    if do_pa:
        print("\n--- PythonAnywhere deploy (repo CSV → server, mtime-aware) ---")
        try:
            _run_pythonanywhere_deploy(csv_only=bool(args.pa_csv_only))
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}")
            return 1
        except subprocess.CalledProcessError as exc:
            print(f"PythonAnywhere deploy failed (exit {exc.returncode}).")
            print("Install deploy deps: py -3 -m pip install -r requirements-deploy.txt")
            print("Set PA_SSH_KEY, PA_USER, PA_REMOTE_PATH, PA_REMOTE_VENV_BIN, PA_WSGI_FILE as needed.")
            return int(exc.returncode or 1)
    elif args.no_pa_deploy:
        print("Skipped PythonAnywhere deploy.")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

