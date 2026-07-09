#!/usr/bin/env python3
"""
STEP 2 — PythonAnywhere push from your PC. If ``paramiko`` is missing, the script prints install
instructions and asks whether to run ``pip install -r requirements-deploy.txt`` for you.

For the usual CSV + import + reload sequence from your machine, prefer
``python scripts/run_site_update.py to-live`` (or omit ``to-live``) so STEP1 and STEP2 stay in order.

  deploy — Upload data/imports/raw + app/static (newer files only), run import_data.py per
           league on the server, then ``reimport_history_sheet_data.py`` (awards + all-stars),
           touch WSGI to reload. Use this for CSVs, images, CSS/JS.

  deploy-db — Upload locally built league SQLite files (+ app/static). Snapshots live OVR on the
              server first, merges those baselines into the local DBs, then uploads so depth-chart
              arrows stay accurate. Skips server-side import_data.py.

  sync   — Upload the whole project tree (newer files only), same rules as before; does NOT
           run imports. Use this for code/template changes without a data refresh.

  deploy — On first run (or after you say locations changed), prompts for each league’s local
           CSV folder; paths are saved to scripts/pythonanywhere_csv_sources.json (gitignored).
           Next runs ask whether locations changed before uploading.

  python scripts/STEP2_pythonanywhere.py deploy --dry-run
  python scripts/STEP2_pythonanywhere.py sync --dry-run

  With no arguments (e.g. Run / F5 in IDLE on this file), ``deploy`` is assumed.

Environment: PA_HOST, PA_USER, PA_REMOTE_PATH, PA_SSH_KEY; for deploy also PA_REMOTE_VENV_BIN,
PA_WSGI_FILE (see --help on each subcommand). PA_AUTO_PIP=1 skips the prompt and runs pip
when paramiko is missing (useful for automation; in non-interactive mode this is required to auto-install).
Encrypted SSH keys: set PA_SSH_PASSPHRASE for this session if your terminal cannot hide input (IDE),
or use ssh-agent; you cannot remove encryption from an existing key without the passphrase.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _ensure_deploy_dependencies() -> None:
    """If paramiko is missing, print install instructions and optionally run pip after prompt."""
    try:
        import paramiko  # noqa: F401
    except ImportError:
        pass
    else:
        return

    req_file = _REPO_ROOT / "requirements-deploy.txt"
    root = str(_REPO_ROOT)
    py_exe = sys.executable

    print("\n" + "=" * 72, file=sys.stderr)
    print("  Missing dependency: paramiko (needed for SFTP/SSH to PythonAnywhere)", file=sys.stderr)
    print("=" * 72 + "\n", file=sys.stderr)
    print("  Install deploy requirements from your project root:\n", file=sys.stderr)
    print(f'    cd "{root}"', file=sys.stderr)
    print(f'    "{py_exe}" -m pip install -r requirements-deploy.txt\n', file=sys.stderr)
    print(
        "  On Windows, if the first command does not work, try:\n",
        file=sys.stderr,
    )
    print(f'    cd /d "{root}"', file=sys.stderr)
    print("    py -3 -m pip install -r requirements-deploy.txt\n", file=sys.stderr)
    print(
        "  (Use the same Python you use to run this script so packages land in the right place.)\n",
        file=sys.stderr,
    )

    if not req_file.is_file():
        print(f"  ERROR: {req_file} not found — cannot install deploy dependencies.", file=sys.stderr)
        raise SystemExit(1)

    auto = os.environ.get("PA_AUTO_PIP", "").strip().lower() in ("1", "true", "yes")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    run_pip = False

    if auto:
        print("  PA_AUTO_PIP is set — running pip install now.\n", file=sys.stderr)
        run_pip = True
    elif interactive:
        try:
            ans = input("  Run pip install now for you with this Python? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        run_pip = ans in ("y", "yes")
    else:
        print(
            "  Not in an interactive terminal — not running pip automatically.\n"
            "  Run one of the commands above, or set PA_AUTO_PIP=1 to allow auto-install.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not run_pip:
        print("  Exiting. After you install, run this script again.\n", file=sys.stderr)
        raise SystemExit(1)

    print(f'  Running: "{py_exe}" -m pip install -r requirements-deploy.txt\n', file=sys.stderr)
    proc = subprocess.run(
        [py_exe, "-m", "pip", "install", "-r", str(req_file)],
        cwd=root,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    try:
        import importlib

        importlib.invalidate_caches()
        import paramiko  # noqa: F401
    except ImportError:
        print(
            "  paramiko still not importable after pip. Check that you used the same Python, or run:\n"
            f'    "{py_exe}" -m pip install paramiko\n',
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("  Dependencies OK — continuing.\n", file=sys.stderr)


_ensure_deploy_dependencies()

try:
    from pa_ssh import connect_sftp, ensure_remote_dir, remote_mtime
except ImportError as e:
    print("Failed to load pa_ssh after installing dependencies.", file=sys.stderr)
    raise SystemExit(1) from e

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "ENV",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "htmlcov",
        ".cursor",
        ".idea",
        ".vscode",
        "node_modules",
    }
)
SKIP_TOP_LEVEL = frozenset({"instance"})
SKIP_FILE_NAMES = frozenset({".env", ".env.local"})
SKIP_DIR_NAMES_DEPLOY_SUBTREE = frozenset({"__pycache__", ".git"})


def repo_root() -> Path:
    return _REPO_ROOT


_CSV_SOURCES_FILE = _SCRIPT_DIR / "pythonanywhere_csv_sources.json"


def _deploy_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def load_saved_csv_sources() -> dict[str, Path] | None:
    from app.config import LEAGUES

    if not _CSV_SOURCES_FILE.is_file():
        return None
    try:
        data = json.loads(_CSV_SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("folders")
    if not isinstance(raw, dict):
        return None
    out: dict[str, Path] = {}
    for e in LEAGUES:
        key = e.raw_import_dir
        if key not in raw or not isinstance(raw[key], str):
            return None
        p = Path(raw[key]).expanduser().resolve()
        if not p.is_dir():
            return None
        out[key] = p
    return out


def save_csv_sources(folders: dict[str, Path]) -> None:
    payload = {
        "version": 1,
        "folders": {k: str(v.resolve()) for k, v in sorted(folders.items())},
    }
    _CSV_SOURCES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved CSV locations to {_CSV_SOURCES_FILE}\n")


def prompt_league_csv_folder(display_name: str, folder_key: str, default_dir: Path) -> Path:
    print(f"\n--- {display_name} ---")
    print(f"  Remote target on server: data/imports/raw/{folder_key}/")
    print(f"  Default (inside project): {default_dir}")
    while True:
        try:
            line = input(
                "  Path to this league's CSV folder on your PC [Enter = default]: "
            ).strip()
        except EOFError:
            line = ""
        line = line.strip('"').strip("'")
        chosen = default_dir if not line else Path(line).expanduser()
        try:
            chosen = chosen.resolve()
        except OSError:
            print(f"  Invalid path: {line!r}\n")
            continue
        if not chosen.is_dir():
            print(f"  Not an existing folder: {chosen}\n")
            continue
        print(f"  Using: {chosen}")
        return chosen


def prompt_configure_all_csv_sources(local_root: Path) -> dict[str, Path]:
    from app.config import LEAGUES

    print(
        "\nRegister where your exported CSV files live for each league.\n"
        "They can be inside this project or anywhere on your disk.\n"
    )
    folders: dict[str, Path] = {}
    for e in LEAGUES:
        default = local_root / "data" / "imports" / "raw" / e.raw_import_dir
        folders[e.raw_import_dir] = prompt_league_csv_folder(
            e.display_name, e.raw_import_dir, default
        )
    return folders


def resolve_csv_sources_for_deploy(
    local_root: Path, args: argparse.Namespace
) -> dict[str, Path]:
    from app.config import LEAGUES

    if getattr(args, "repo_csv", False):
        folders: dict[str, Path] = {}
        for e in LEAGUES:
            p = (local_root / "data" / "imports" / "raw" / e.raw_import_dir).resolve()
            if not p.is_dir():
                raise SystemExit(
                    f"--repo-csv: missing folder {p}\n"
                    "Use the wizard (omit --repo-csv) or create that directory."
                )
            folders[e.raw_import_dir] = p
        return folders

    loaded = load_saved_csv_sources()

    if _deploy_interactive():
        if loaded:
            try:
                ans = input(
                    "\nHave the CSV folder locations changed since last time? [y/N] "
                ).strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("\nUsing saved CSV folder locations:")
                for e in LEAGUES:
                    print(f"  {e.display_name}: {loaded[e.raw_import_dir]}")
                print()
                return loaded
            print("\nEnter the new folder for each league.\n")
        else:
            print(
                "\nNo saved CSV locations yet — confirm once where each league's CSVs live.\n"
            )

        folders = prompt_configure_all_csv_sources(local_root)
        save_csv_sources(folders)
        return folders

    if loaded:
        return loaded
    raise SystemExit(
        "No saved CSV paths (scripts/pythonanywhere_csv_sources.json).\n"
        "Run `python scripts/STEP2_pythonanywhere.py deploy` once in an interactive terminal to set them,\n"
        "or pass --repo-csv to use the project's data/imports/raw/<league>/ folders."
    )


def iter_files_under_dir(root: Path) -> list[Path]:
    import os as _os

    root = root.resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in _os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES_DEPLOY_SUBTREE]
        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


def upload_league_raw_folders(
    sftp,
    league_roots: dict[str, Path],
    remote_base: str,
    *,
    dry_run: bool,
    force: bool,
    skew_seconds: float,
) -> tuple[int, int]:
    from app.config import LEAGUES

    uploaded = 0
    skipped = 0
    remote_base = remote_base.rstrip("/")
    for e in LEAGUES:
        key = e.raw_import_dir
        src_root = league_roots[key]
        for local_path in iter_files_under_dir(src_root):
            rel_within = local_path.relative_to(src_root)
            remote_rel = f"data/imports/raw/{key}/{rel_within.as_posix()}"
            remote_file = f"{remote_base}/{remote_rel}"
            local_mtime = local_path.stat().st_mtime
            rmt = None if force else remote_mtime(sftp, remote_file)
            if rmt is not None and local_mtime <= rmt + skew_seconds:
                skipped += 1
                continue
            if dry_run:
                print(f"would upload {remote_rel}")
                uploaded += 1
                continue
            remote_parent = str(PurePosixPath(remote_file).parent)
            ensure_remote_dir(sftp, remote_parent)
            sftp.put(str(local_path), remote_file)
            print(f"upload {remote_rel}")
            uploaded += 1
    return uploaded, skipped


def should_skip_path(rel: PurePosixPath, include_instance: bool) -> bool:
    parts = rel.parts
    if not parts:
        return True
    if not include_instance and parts[0] in SKIP_TOP_LEVEL:
        return True
    for p in parts:
        if p in SKIP_DIR_NAMES:
            return True
    return False


def iter_local_files(local_root: Path, include_instance: bool) -> list[Path]:
    import os as _os

    out: list[Path] = []
    local_root = local_root.resolve()
    for dirpath, dirnames, filenames in _os.walk(local_root, topdown=True):
        rel_path = Path(dirpath).relative_to(local_root)
        dirnames[:] = [
            d
            for d in dirnames
            if not should_skip_path(
                PurePosixPath((rel_path / d).as_posix()), include_instance
            )
        ]
        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            rel = PurePosixPath((rel_path / name).as_posix())
            if should_skip_path(rel, include_instance):
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


def iter_subtree_files(local_root: Path, rel_subdir: str) -> list[Path]:
    import os as _os

    base = (local_root / rel_subdir).resolve()
    if not base.is_dir():
        raise SystemExit(f"Missing directory: {base}")
    out: list[Path] = []
    for dirpath, dirnames, filenames in _os.walk(base, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES_DEPLOY_SUBTREE]
        for name in filenames:
            if name in SKIP_FILE_NAMES:
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


_DEPLOY_MANIFEST_VERSION = 1
_UPLOAD_PROGRESS_EVERY = 500


def _tree_manifest_path(local_root: Path, rel_subdir: str) -> Path:
    safe = rel_subdir.replace("/", "_").replace("\\", "_").strip("_") or "root"
    return local_root / "instance" / f".deploy_manifest_{safe}.json"


def _local_file_signature(st: os.stat_result) -> dict[str, float | int]:
    return {"mtime": float(st.st_mtime), "size": int(st.st_size)}


def _load_tree_manifest(path: Path, rel_subdir: str) -> dict[str, dict[str, float | int]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("version") != _DEPLOY_MANIFEST_VERSION or data.get("tree") != rel_subdir:
        return {}
    files = data.get("files")
    return dict(files) if isinstance(files, dict) else {}


def _save_tree_manifest(
    path: Path, rel_subdir: str, files: dict[str, dict[str, float | int]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _DEPLOY_MANIFEST_VERSION, "tree": rel_subdir, "files": files}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def seed_tree_manifest(local_root: Path, rel_subdir: str) -> int:
    """Record local mtime/size for every file under rel_subdir (server assumed in sync)."""
    files: dict[str, dict[str, float | int]] = {}
    for local_path in iter_subtree_files(local_root, rel_subdir):
        rel_posix = local_path.relative_to(local_root).as_posix()
        files[rel_posix] = _local_file_signature(local_path.stat())
    manifest_path = _tree_manifest_path(local_root, rel_subdir)
    _save_tree_manifest(manifest_path, rel_subdir, files)
    print(
        f"Seeded deploy manifest for {len(files)} file(s): "
        f"{manifest_path.relative_to(local_root).as_posix()}"
    )
    return len(files)


def upload_tree(
    sftp,
    local_root: Path,
    rel_subdir: str,
    remote_base: str,
    *,
    dry_run: bool,
    force: bool,
    skew_seconds: float,
) -> tuple[int, int]:
    """Upload files under rel_subdir that changed since the last successful deploy.

    A local manifest (``instance/.deploy_manifest_<tree>.json``) stores mtime+size for
    files already on the server. Unchanged files skip without an SFTP round trip. Files
    missing from the manifest still use remote mtime once, then are recorded for next time.
  """
    uploaded = 0
    skipped = 0
    manifest_path = _tree_manifest_path(local_root, rel_subdir)
    manifest: dict[str, dict[str, float | int]] = (
        {} if force else _load_tree_manifest(manifest_path, rel_subdir)
    )
    manifest_had_entries = bool(manifest)
    manifest_dirty = False
    local_files = iter_subtree_files(local_root, rel_subdir)
    if manifest_had_entries:
        print(
            f"  {rel_subdir}: using deploy manifest "
            f"({len(manifest)} known file(s); only changes are checked remotely)"
        )
    elif not force and not dry_run:
        print(
            f"  {rel_subdir}: no deploy manifest yet — first pass may take a while "
            f"({len(local_files)} file(s); building manifest as we go)"
        )

    for idx, local_path in enumerate(local_files, 1):
        rel = local_path.relative_to(local_root)
        rel_posix = rel.as_posix()
        remote_file = f"{remote_base}/{rel_posix}"
        sig = _local_file_signature(local_path.stat())
        local_mtime = float(sig["mtime"])

        if not force and manifest.get(rel_posix) == sig:
            skipped += 1
            if idx % _UPLOAD_PROGRESS_EVERY == 0:
                print(
                    f"  {rel_subdir}: {idx}/{len(local_files)} "
                    f"({uploaded} upload, {skipped} skip)"
                )
            continue

        if not force:
            rmt = remote_mtime(sftp, remote_file)
            if rmt is not None and local_mtime <= rmt + skew_seconds:
                skipped += 1
                manifest[rel_posix] = sig
                manifest_dirty = True
                if idx % _UPLOAD_PROGRESS_EVERY == 0:
                    print(
                        f"  {rel_subdir}: {idx}/{len(local_files)} "
                        f"({uploaded} upload, {skipped} skip)"
                    )
                continue
        if dry_run:
            print(f"would upload {rel_posix}")
            uploaded += 1
            continue
        remote_parent = str(PurePosixPath(remote_file).parent)
        ensure_remote_dir(sftp, remote_parent)
        sftp.put(str(local_path), remote_file)
        print(f"upload {rel_posix}")
        uploaded += 1
        manifest[rel_posix] = sig
        manifest_dirty = True

    if manifest_dirty and not dry_run:
        _save_tree_manifest(manifest_path, rel_subdir, manifest)

    return uploaded, skipped


def upload_named_repo_files(
    sftp,
    local_root: Path,
    relative_paths: tuple[str, ...],
    remote_base: str,
    *,
    dry_run: bool,
    force: bool,
    skew_seconds: float,
) -> tuple[int, int]:
    """Upload specific repo files (mtime-aware) so remote import steps can rely on them."""
    uploaded = 0
    skipped = 0
    remote_base = remote_base.rstrip("/")
    for rel in relative_paths:
        local_path = (local_root / rel).resolve()
        if not local_path.is_file():
            continue
        remote_file = f"{remote_base}/{rel}"
        local_mtime = local_path.stat().st_mtime
        rmt = None if force else remote_mtime(sftp, remote_file)
        if rmt is not None and local_mtime <= rmt + skew_seconds:
            skipped += 1
            continue
        if dry_run:
            print(f"would upload {rel}")
            uploaded += 1
            continue
        remote_parent = str(PurePosixPath(remote_file).parent)
        ensure_remote_dir(sftp, remote_parent)
        sftp.put(str(local_path), remote_file)
        print(f"upload {rel}")
        uploaded += 1
    return uploaded, skipped


def run_remote_bash(client, script_body: str) -> None:
    cmd = "bash -lc " + shlex.quote(script_body)
    _stdin, stdout, stderr = client.exec_command(cmd)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    if out_b:
        sys.stdout.write(out_b.decode(errors="replace"))
    if err_b:
        sys.stderr.write(err_b.decode(errors="replace"))
    if code != 0:
        raise SystemExit(f"Remote command failed with exit code {code}")


def build_import_and_reload_script(
    remote_project: str,
    venv_bin: str,
    slugs: list[str],
    wsgi_file: str | None,
    *,
    install_requirements: bool = False,
) -> str:
    rp = shlex.quote(remote_project.rstrip("/"))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    imp = shlex.quote(f"{remote_project.rstrip('/')}/scripts/import_data.py")
    sheet_rel = "scripts/reimport_history_sheet_data.py"
    req = shlex.quote(f"{remote_project.rstrip('/')}/requirements.txt")
    parts = ["set -euo pipefail", f"cd {rp}", f". {act}"]
    if install_requirements:
        parts.append(f"{py} -m pip install --upgrade -r {req}")
    for slug in slugs:
        parts.append(f"export LEAGUE_SLUG={shlex.quote(slug)}")
        parts.append(f"{py} {imp} {shlex.quote(slug)} --repair-sqlite")
        parts.append(
            f"if test -f {shlex.quote(sheet_rel)}; then {py} {shlex.quote(sheet_rel)} {shlex.quote(slug)}; "
            f"else echo {shlex.quote('WARN: missing ' + sheet_rel + ' — git pull on server or upgrade repo')}; fi"
        )
    if wsgi_file:
        parts.append(f"touch {shlex.quote(wsgi_file)}")
    return "; ".join(parts)


def build_snapshot_ovr_baselines_script(remote_project: str, venv_bin: str, slugs: list[str]) -> str:
    """SSH script: snapshot OVR baselines on the server while remote CSVs are still pre-upload."""
    rp = shlex.quote(remote_project.rstrip("/"))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    snap = shlex.quote(f"{remote_project.rstrip('/')}/scripts/snapshot_ovr_baseline.py")
    parts = ["set -euo pipefail", f"cd {rp}", f". {act}"]
    for slug in slugs:
        parts.append(f"export LEAGUE_SLUG={shlex.quote(slug)}")
        parts.append(f"{py} {snap}")
    return "; ".join(parts)


def _deploy_ovr_baseline_json_name(slug: str) -> str:
    return f".deploy_ovr_baselines_{slug}.json"


def build_capture_live_ovr_baselines_script(
    remote_project: str,
    venv_bin: str,
    slugs: list[str],
) -> str:
    """SSH script: snapshot live OVR, then export baselines JSON keyed by fhm_player_id."""
    rp = shlex.quote(remote_project.rstrip("/"))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    snap = shlex.quote(f"{remote_project.rstrip('/')}/scripts/snapshot_ovr_baseline.py")
    transfer = shlex.quote(f"{remote_project.rstrip('/')}/scripts/ovr_baseline_transfer.py")
    parts = ["set -euo pipefail", f"cd {rp}", f". {act}"]
    for slug in slugs:
        out_name = _deploy_ovr_baseline_json_name(slug)
        parts.append(f"export LEAGUE_SLUG={shlex.quote(slug)}")
        parts.append(f"{py} {snap}")
        parts.append(
            f"{py} {transfer} export --slug {shlex.quote(slug)} "
            f"--out {shlex.quote(f'instance/{out_name}')}"
        )
    return "; ".join(parts)


def build_post_db_upload_script(
    remote_project: str,
    venv_bin: str,
    slugs: list[str],
    wsgi_file: str | None,
    *,
    staged_db_rels: tuple[str, ...] = (),
) -> str:
    """SSH script: atomically promote staged DBs, drop WAL sidecars, integrity-check, reload WSGI."""
    rp = shlex.quote(remote_project.rstrip("/"))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    repair = shlex.quote(f"{remote_project.rstrip('/')}/scripts/repair_league_sqlite.py")
    parts = ["set -euo pipefail", f"cd {rp}", f". {act}"]
    for remote_rel in staged_db_rels:
        final = shlex.quote(f"{remote_project.rstrip('/')}/{remote_rel}")
        staging = shlex.quote(f"{remote_project.rstrip('/')}/{remote_rel}.deploy-upload")
        parts.append(f"rm -f {final}-wal {final}-shm")
        parts.append(f"mv {staging} {final}")
    for slug in slugs:
        parts.append(
            f"{py} -c {shlex.quote(_remote_remove_wal_sidecars_code(slug))}"
        )
        parts.append(f"{py} {repair} --check --league {shlex.quote(slug)}")
    if wsgi_file:
        parts.append(f"touch {shlex.quote(wsgi_file)}")
    return "; ".join(parts)


def _remote_remove_wal_sidecars_code(slug: str) -> str:
    return (
        "from pathlib import Path; "
        "from app.config import resolve_league_sqlite_path; "
        f"p = resolve_league_sqlite_path({slug!r}); "
        "Path(str(p) + '-wal').unlink(missing_ok=True); "
        "Path(str(p) + '-shm').unlink(missing_ok=True); "
        "print('removed WAL sidecars for', p.name)"
    )


def league_db_upload_targets(local_root: Path, slugs: list[str]) -> list[tuple[str, Path, str]]:
    """Return (slug, local_db_path, remote_rel_path) for each league database to upload."""
    from app.config import _LEGACY_LEAGUE_DB_FILES, resolve_league_sqlite_path

    out: list[tuple[str, Path, str]] = []
    seen_remote: set[str] = set()
    for slug in slugs:
        db_path = resolve_league_sqlite_path(slug).resolve()
        if not db_path.is_file():
            raise SystemExit(f"Missing local league database for {slug}: {db_path}")
        remote_rel = f"instance/{db_path.name}"
        if remote_rel not in seen_remote:
            seen_remote.add(remote_rel)
            out.append((slug, db_path, remote_rel))
        # Production prefers ``instance/<slug>.db`` when populated; local imports may still
        # target the legacy filename (e.g. league3.db). Mirror the upload so both paths match.
        legacy_name = _LEGACY_LEAGUE_DB_FILES.get(slug)
        if legacy_name and db_path.name == legacy_name:
            primary_rel = f"instance/{slug}.db"
            if primary_rel not in seen_remote:
                seen_remote.add(primary_rel)
                out.append((slug, db_path, primary_rel))
    return out


def build_full_remote_rebuild_prep_script(
    remote_project: str,
    venv_bin: str,
    wsgi_file: str | None,
) -> str:
    """Hard-reset repo + rebuild venv on PythonAnywhere before normal deploy/import flow."""
    rp = shlex.quote(remote_project.rstrip("/"))
    # venv_bin is .../venv/bin — remove and recreate the .../venv directory only (not its grandparent).
    venv_root = shlex.quote(str(PurePosixPath(venv_bin.rstrip("/")).parent))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    req = shlex.quote(f"{remote_project.rstrip('/')}/requirements.txt")
    parts = [
        "set -euo pipefail",
        f"cd {rp}",
        "mv tests/init.py /tmp/testsinit.py.bak 2>/dev/null || true",
        "git fetch origin",
        "git checkout master",
        "git reset --hard origin/master",
        f"rm -rf {venv_root}",
        f"python3.11 -m venv {venv_root}",
        f". {act}",
        f"{py} -m pip install --upgrade pip",
        f"{py} -m pip install --upgrade -r {req}",
        f"{py} -c \"import flask, flask_login, flask_sqlalchemy, flask_wtf, werkzeug; print('imports ok')\"",
        f"{py} -c \"import sys; print(sys.executable)\"",
    ]
    if wsgi_file:
        parts.append(f"touch {shlex.quote(wsgi_file)}")
    return "; ".join(parts)


def build_remote_ap_catalog_export_script(
    remote_project: str,
    venv_bin: str,
    out_name: str = "ap_redemption_catalog_live.json",
) -> str:
    rp = shlex.quote(remote_project.rstrip("/"))
    act = shlex.quote(f"{venv_bin.rstrip('/')}/activate")
    py = shlex.quote(f"{venv_bin.rstrip('/')}/python")
    code = (
        "import json, sqlite3, pathlib; "
        "p=pathlib.Path('instance/site_membership.db'); "
        "c=sqlite3.connect(p); c.row_factory=sqlite3.Row; "
        "rows=[dict(r) for r in c.execute("
        "'select league_group,sort_order,title,description,cost_ap,is_active "
        "from ap_redemption_catalog order by league_group,cost_ap,sort_order,id'"
        ")]; "
        f"pathlib.Path({out_name!r}).write_text(json.dumps(rows, indent=2), encoding='utf-8'); "
        "print(f'Exported {len(rows)} AP catalog rows')"
    )
    return "; ".join(
        [
            "set -euo pipefail",
            f"cd {rp}",
            f". {act}",
            f"{py} -c {shlex.quote(code)}",
        ]
    )


def sync_local_ap_catalog_from_remote(
    client,
    sftp,
    *,
    local_root: Path,
    remote_project: str,
    venv_bin: str,
    dry_run: bool,
) -> None:
    local_json = local_root / "ap_redemption_catalog_live.json"
    remote_json = f"{remote_project.rstrip('/')}/ap_redemption_catalog_live.json"
    if dry_run:
        print("--- would sync AP catalog to local ---")
        print(f"would run remote export to {remote_json}")
        print(f"would download to {local_json}")
        print(
            f"would run: {sys.executable} scripts/import_ap_catalog.py --in {local_json.name}"
        )
        print(
            f"would run: {sys.executable} scripts/verify_ap_catalog_sync.py --in {local_json.name}"
        )
        return

    print("--- sync AP catalog (live -> local) ---")
    export_script = build_remote_ap_catalog_export_script(remote_project, venv_bin)
    run_remote_bash(client, export_script)
    sftp.get(remote_json, str(local_json))
    print(f"Downloaded {remote_json} -> {local_json}")

    subprocess.run(
        [sys.executable, "scripts/import_ap_catalog.py", "--in", local_json.name],
        cwd=str(local_root),
        check=True,
    )
    subprocess.run(
        [sys.executable, "scripts/verify_ap_catalog_sync.py", "--in", local_json.name],
        cwd=str(local_root),
        check=True,
    )


def add_connection_args(p: argparse.ArgumentParser, default_remote: str, default_user: str) -> None:
    p.add_argument("--local-root", type=Path, default=_REPO_ROOT, help="Repo root")
    p.add_argument("--host", default=os.environ.get("PA_HOST", "ssh.pythonanywhere.com"))
    p.add_argument("--user", default=default_user)
    p.add_argument("--remote-path", default=default_remote)
    p.add_argument(
        "--key",
        type=Path,
        default=Path(os.environ["PA_SSH_KEY"]) if os.environ.get("PA_SSH_KEY") else None,
    )


def cmd_sync(ns: argparse.Namespace) -> int:
    local_root = ns.local_root.resolve()
    if not (local_root / "wsgi.py").is_file():
        print(
            f"Warning: no wsgi.py under {local_root} — is this the project root?",
            file=sys.stderr,
        )
    remote_base = ns.remote_path.rstrip("/")
    files = iter_local_files(local_root, ns.include_instance)
    client = None
    uploaded = 0
    skipped = 0
    try:
        client, sftp = connect_sftp(ns.host, ns.user, ns.key)
        for local_path in files:
            rel = local_path.relative_to(local_root)
            remote_file = f"{remote_base}/{rel.as_posix()}"
            local_mtime = local_path.stat().st_mtime
            rmt = None if ns.force else remote_mtime(sftp, remote_file)
            if rmt is not None and local_mtime <= rmt + ns.skew_seconds:
                skipped += 1
                continue
            if ns.dry_run:
                uploaded += 1
                print(f"would upload {rel.as_posix()}")
                continue
            remote_parent = str(PurePosixPath(remote_file).parent)
            ensure_remote_dir(sftp, remote_parent)
            sftp.put(str(local_path), remote_file)
            uploaded += 1
            print(f"upload {rel.as_posix()}")
    finally:
        if client is not None:
            client.close()
    if ns.dry_run:
        print(
            f"Dry run done. Would upload {uploaded}, skip {skipped} (remote same or newer)."
        )
    else:
        print(f"Done. Uploaded {uploaded}, skipped (already current) {skipped}.")
    return 0


def cmd_deploy(ns: argparse.Namespace) -> int:
    from app.config import LEAGUES, league_slugs

    local_root = ns.local_root.resolve()
    remote_base = ns.remote_path.rstrip("/")
    slugs = league_slugs()
    if not slugs:
        raise SystemExit("league_slugs() returned no leagues.")
    if getattr(ns, "seed_static_manifest", False):
        seed_tree_manifest(local_root, "app/static")
    csv_roots = resolve_csv_sources_for_deploy(local_root, ns)
    for entry in LEAGUES:
        if not iter_files_under_dir(csv_roots[entry.raw_import_dir]):
            print(
                f"Warning: no files found for {entry.display_name} in {csv_roots[entry.raw_import_dir]}",
                file=sys.stderr,
            )
    wsgi = None if ns.skip_reload else ns.wsgi_file
    print("--- deploy target ---")
    print(f"host: {ns.host}")
    print(f"user: {ns.user}")
    print(f"remote project: {remote_base}")
    print(f"remote venv bin: {ns.venv_bin}")
    print(f"wsgi file: {wsgi or '(skip reload)'}")
    print(f"remote pip install: {'yes' if ns.remote_pip else 'no'}")
    print(f"full remote rebuild: {'yes' if ns.full_remote_rebuild else 'no'}")
    script = build_import_and_reload_script(
        remote_base,
        ns.venv_bin,
        slugs,
        wsgi,
        install_requirements=bool(ns.remote_pip),
    )
    client = None
    total_up = 0
    total_skip = 0
    try:
        client, sftp = connect_sftp(ns.host, ns.user, ns.key)
        if ns.full_remote_rebuild:
            prep_script = build_full_remote_rebuild_prep_script(remote_base, ns.venv_bin, wsgi)
            if ns.dry_run:
                print("--- would run full remote rebuild prep ---")
                print(prep_script.replace("; ", "\n"))
            else:
                print("--- full remote rebuild prep ---")
                run_remote_bash(client, prep_script)
                print("Full remote rebuild prep finished.")
        if not ns.skip_imports and not ns.dry_run and slugs:
            snap_script = build_snapshot_ovr_baselines_script(remote_base, ns.venv_bin, slugs)
            print("--- remote OVR baseline snapshot (before CSV upload) ---")
            run_remote_bash(client, snap_script)
        elif not ns.skip_imports and ns.dry_run and slugs:
            print("--- would run remote OVR baseline snapshot (before CSV upload) ---")
            print(build_snapshot_ovr_baselines_script(remote_base, ns.venv_bin, slugs).replace("; ", "\n"))
        print("--- data/imports/raw (from your registered folders) ---")
        u, s = upload_league_raw_folders(
            sftp,
            csv_roots,
            remote_base,
            dry_run=ns.dry_run,
            force=ns.force,
            skew_seconds=ns.skew_seconds,
        )
        total_up += u
        total_skip += s
        print("--- scripts (import helpers) ---")
        su, ss = upload_named_repo_files(
            sftp,
            local_root,
            ("scripts/reimport_history_sheet_data.py",),
            remote_base,
            dry_run=ns.dry_run,
            force=ns.force,
            skew_seconds=ns.skew_seconds,
        )
        total_up += su
        total_skip += ss
        if not ns.csv_only:
            print("--- app/static ---")
            u, s = upload_tree(
                sftp,
                local_root,
                "app/static",
                remote_base,
                dry_run=ns.dry_run,
                force=ns.force,
                skew_seconds=ns.skew_seconds,
            )
            total_up += u
            total_skip += s
        if ns.skip_imports and ns.skip_reload:
            print("Skip imports and reload.")
        elif ns.dry_run:
            print("--- would run on server ---")
            if ns.skip_imports:
                print("(imports skipped)")
                if wsgi:
                    print(f"touch {wsgi}")
            else:
                print(script.replace("; ", "\n"))
        else:
            if ns.skip_imports:
                if wsgi:
                    run_remote_bash(client, f"touch {shlex.quote(wsgi)}")
                    print(f"Reload: touched {wsgi}")
            else:
                print("--- remote imports (+ reload) ---")
                run_remote_bash(client, script)
                print("Remote imports finished.")
        if ns.sync_ap_catalog_local:
            sync_local_ap_catalog_from_remote(
                client,
                sftp,
                local_root=local_root,
                remote_project=remote_base,
                venv_bin=ns.venv_bin,
                dry_run=bool(ns.dry_run),
            )
    finally:
        if client is not None:
            client.close()
    print(
        f"Done. Uploaded {total_up}, skipped {total_skip} (remote same or newer)."
    )
    return 0


def cmd_deploy_db(ns: argparse.Namespace) -> int:
    """Upload locally built league SQLite files; preserve live OVR baselines for trend arrows."""
    from app.config import league_slugs
    from app.db_utils import sqlite_integrity_message, sqlite_wal_checkpoint

    local_root = ns.local_root.resolve()
    remote_base = ns.remote_path.rstrip("/")
    slugs = league_slugs()
    if not slugs:
        raise SystemExit("league_slugs() returned no leagues.")

    if getattr(ns, "seed_static_manifest", False):
        seed_tree_manifest(local_root, "app/static")

    print("--- deploy-db (local SQLite upload) ---")
    print(f"host: {ns.host}")
    print(f"user: {ns.user}")
    print(f"remote project: {remote_base}")
    print(f"remote venv bin: {ns.venv_bin}")
    wsgi = None if ns.skip_reload else ns.wsgi_file
    print(f"wsgi file: {wsgi or '(skip reload)'}")

    try:
        db_targets = league_db_upload_targets(local_root, slugs)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("--- local database preflight ---")
    for slug, db_path, remote_rel in db_targets:
        sqlite_wal_checkpoint(db_path)
        msg = sqlite_integrity_message(db_path)
        if msg.lower() != "ok":
            print(f"ERROR: {db_path} integrity check failed: {msg}", file=sys.stderr)
            return 1
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"  {slug}: {db_path.name} ({size_mb:.1f} MB) -> {remote_rel}")

    baseline_dir = local_root / "instance" / ".deploy_ovr_baselines"
    capture_script = build_capture_live_ovr_baselines_script(remote_base, ns.venv_bin, slugs)
    staged_db_rels = tuple(remote_rel for _slug, _db_path, remote_rel in db_targets)
    post_upload_script = build_post_db_upload_script(
        remote_base, ns.venv_bin, slugs, wsgi, staged_db_rels=staged_db_rels
    )

    if ns.dry_run:
        print("--- would upload helper scripts ---")
        print("would upload scripts/ovr_baseline_transfer.py")
        print("--- would capture live OVR baselines on server ---")
        print(capture_script.replace("; ", "\n"))
        print("--- would download live baseline JSON ---")
        for slug in slugs:
            print(f"would download instance/{_deploy_ovr_baseline_json_name(slug)}")
        print("--- would merge live baselines into local DBs ---")
        for slug in slugs:
            print(
                f"would run: {sys.executable} scripts/ovr_baseline_transfer.py import "
                f"--slug {slug} --in instance/.deploy_ovr_baselines/{_deploy_ovr_baseline_json_name(slug)}"
            )
        print("--- would upload league databases ---")
        for _slug, db_path, remote_rel in db_targets:
            print(f"would upload {remote_rel} ({db_path.name})")
        if not ns.skip_static:
            print("--- would upload app/static (newer files only) ---")
        print("--- would run on server after upload ---")
        print(post_upload_script.replace("; ", "\n"))
        return 0

    client = None
    uploaded = 0
    try:
        client, sftp = connect_sftp(ns.host, ns.user, ns.key)
        su, ss = upload_named_repo_files(
            sftp,
            local_root,
            (
                "scripts/ovr_baseline_transfer.py",
                "scripts/snapshot_ovr_baseline.py",
                "scripts/repair_league_sqlite.py",
            ),
            remote_base,
            dry_run=False,
            force=True,
            skew_seconds=ns.skew_seconds,
        )
        uploaded += su
        print(f"Helper scripts uploaded ({su} files, {ss} skipped).")

        print("--- capture live OVR baselines on server ---")
        run_remote_bash(client, capture_script)

        baseline_dir.mkdir(parents=True, exist_ok=True)
        print("--- download live baseline JSON ---")
        for slug in slugs:
            remote_json = f"{remote_base}/instance/{_deploy_ovr_baseline_json_name(slug)}"
            local_json = baseline_dir / _deploy_ovr_baseline_json_name(slug)
            sftp.get(remote_json, str(local_json))
            print(f"download {remote_json} -> {local_json.relative_to(local_root).as_posix()}")

        print("--- merge live baselines into local databases ---")
        transfer = local_root / "scripts" / "ovr_baseline_transfer.py"
        for slug in slugs:
            local_json = baseline_dir / _deploy_ovr_baseline_json_name(slug)
            subprocess.run(
                [sys.executable, str(transfer), "import", "--slug", slug, "--in", str(local_json)],
                cwd=str(local_root),
                check=True,
            )
            sqlite_wal_checkpoint(resolve_league_db_path(slug, db_targets))

        print("--- upload league databases (staged) ---")
        for slug, db_path, remote_rel in db_targets:
            remote_file = f"{remote_base}/{remote_rel}"
            remote_staging = f"{remote_file}.deploy-upload"
            sqlite_wal_checkpoint(db_path)
            remote_parent = str(PurePosixPath(remote_file).parent)
            ensure_remote_dir(sftp, remote_parent)
            sftp.put(str(db_path), remote_staging)
            uploaded += 1
            print(f"upload {remote_rel} (staging)")

        if not ns.skip_static:
            print("--- app/static ---")
            u, s = upload_tree(
                sftp,
                local_root,
                "app/static",
                remote_base,
                dry_run=False,
                force=ns.force,
                skew_seconds=ns.skew_seconds,
            )
            uploaded += u
            print(f"Static upload: {u} uploaded, {s} skipped.")

        print("--- remote post-upload checks + reload ---")
        run_remote_bash(client, post_upload_script)

        if ns.sync_ap_catalog_local:
            sync_local_ap_catalog_from_remote(
                client,
                sftp,
                local_root=local_root,
                remote_project=remote_base,
                venv_bin=ns.venv_bin,
                dry_run=False,
            )
    finally:
        if client is not None:
            client.close()

    print(f"deploy-db complete. Uploaded {uploaded} file(s).")
    return 0


def resolve_league_db_path(slug: str, db_targets: list[tuple[str, Path, str]]) -> Path:
    for s, path, _rel in db_targets:
        if s == slug:
            return path
    from app.config import resolve_league_sqlite_path

    return resolve_league_sqlite_path(slug)


def main() -> int:
    # IDLE "Run module" / F5 only passes the script path — default to deploy.
    if len(sys.argv) == 1:
        sys.argv.append("deploy")

    default_remote = os.environ.get(
        "PA_REMOTE_PATH",
        "/home/BoiledEgg1974/boys-of-winter-hockey-website",
    )
    default_user = os.environ.get("PA_USER", "BoiledEgg1974")
    default_venv_bin = os.environ.get("PA_REMOTE_VENV_BIN", f"/home/{default_user}/venv/bin")
    default_wsgi = os.environ.get("PA_WSGI_FILE", f"/var/www/{default_user}_wsgi.py")

    parser = argparse.ArgumentParser(
        description=(
            "Push to PythonAnywhere: use 'deploy-db' for local SQLite upload, "
            "'deploy' for CSVs/static+imports, 'sync' for full code tree."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser(
        "sync",
        help="Upload whole project (newer files only); does not run imports.",
    )
    add_connection_args(p_sync, default_remote, default_user)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--force", action="store_true")
    p_sync.add_argument("--include-instance", action="store_true")
    p_sync.add_argument("--skew-seconds", type=float, default=2.0)
    p_sync.set_defaults(func=cmd_sync)

    p_deploy = sub.add_parser(
        "deploy",
        help="Upload CSVs + app/static, run imports on server, reload web app.",
    )
    add_connection_args(p_deploy, default_remote, default_user)
    p_deploy.add_argument(
        "--venv-bin",
        default=default_venv_bin,
        help="Remote venv bin (contains activate and python)",
    )
    p_deploy.add_argument("--wsgi-file", default=default_wsgi)
    p_deploy.add_argument("--csv-only", action="store_true")
    p_deploy.add_argument(
        "--repo-csv",
        action="store_true",
        help="Use project data/imports/raw/<league>/ only; skip prompts and saved paths.",
    )
    p_deploy.add_argument("--skip-imports", action="store_true")
    p_deploy.add_argument("--skip-reload", action="store_true")
    p_deploy.add_argument(
        "--remote-pip",
        action="store_true",
        help="Run remote `python -m pip install -r requirements.txt` before imports.",
    )
    p_deploy.add_argument("--dry-run", action="store_true")
    p_deploy.add_argument("--force", action="store_true")
    p_deploy.add_argument("--skew-seconds", type=float, default=2.0)
    p_deploy.add_argument(
        "--seed-static-manifest",
        action="store_true",
        help=(
            "Before upload, record local app/static mtime+size as already deployed "
            "(one-time bootstrap when the server already matches your PC)."
        ),
    )
    p_deploy.add_argument(
        "--sync-ap-catalog-local",
        action="store_true",
        help=(
            "After deploy, export live ap_redemption_catalog, download JSON to repo root, "
            "import into local DB, then verify."
        ),
    )
    p_deploy.add_argument(
        "--full-remote-rebuild",
        action="store_true",
        help=(
            "Run remote hard reset + venv rebuild prep before normal deploy/import flow "
            "(recovery mode)."
        ),
    )
    p_deploy.set_defaults(func=cmd_deploy)

    p_deploy_db = sub.add_parser(
        "deploy-db",
        help="Upload locally built league SQLite files (+ optional static); preserve live OVR arrows.",
    )
    add_connection_args(p_deploy_db, default_remote, default_user)
    p_deploy_db.add_argument(
        "--venv-bin",
        default=default_venv_bin,
        help="Remote venv bin (contains activate and python)",
    )
    p_deploy_db.add_argument("--wsgi-file", default=default_wsgi)
    p_deploy_db.add_argument("--skip-static", action="store_true", help="Upload databases only.")
    p_deploy_db.add_argument("--skip-reload", action="store_true")
    p_deploy_db.add_argument("--dry-run", action="store_true")
    p_deploy_db.add_argument("--force", action="store_true", help="Always upload app/static files.")
    p_deploy_db.add_argument("--skew-seconds", type=float, default=2.0)
    p_deploy_db.add_argument(
        "--seed-static-manifest",
        action="store_true",
        help=(
            "Before upload, record local app/static mtime+size as already deployed "
            "(one-time bootstrap when the server already matches your PC)."
        ),
    )
    p_deploy_db.add_argument(
        "--sync-ap-catalog-local",
        action="store_true",
        help=(
            "After deploy, export live ap_redemption_catalog, download JSON to repo root, "
            "import into local DB, then verify."
        ),
    )
    p_deploy_db.set_defaults(func=cmd_deploy_db)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
