"""Interactive updater: copy CSV exports, import all leagues, optionally git push.

Run directly:
    python scripts/update_from_saved_game.py
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
    _save_paths(DEFAULT_SOURCES)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy saved-game CSVs, import leagues, and optionally push.")
    parser.add_argument("--base", type=str, default=None, help="Base export folder containing league subfolders.")
    parser.add_argument("--historical", type=str, default=None, help="Override source folder for BOWL-Historical.")
    parser.add_argument("--fantasy", type=str, default=None, help="Override source folder for BOWL-Fantasy.")
    parser.add_argument("--cap", type=str, default=None, help="Override source folder for BOWL-Cap.")
    parser.add_argument("--yes-push", action="store_true", help="Commit and push automatically (no prompt).")
    parser.add_argument("--no-push", action="store_true", help="Skip commit and push automatically (no prompt).")
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
        changed_raw = input("Have your saved-game CSV paths changed since last run? [y/N]: ").strip().lower()
        paths_changed = changed_raw in {"y", "yes"}
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
    if args.yes_push:
        do_git = True
    elif args.no_push:
        do_git = False
    else:
        do_git = input("\nCommit and push to GitHub now? [y/N]: ").strip().lower() == "y"
    if do_git:
        try:
            _git_commit_and_push()
        except subprocess.CalledProcessError as exc:
            print(f"Git command failed: {exc}")
            return int(exc.returncode or 1)
    else:
        print("Skipped git commit/push.")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

