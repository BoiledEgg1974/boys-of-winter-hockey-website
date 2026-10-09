#!/usr/bin/env python3
"""Push application code from this repo to the live VPS (no SQLite / .env).

Use after ``git pull`` locally when Python on the droplet should match GitHub.
League data still updates via ``BOWL-Site-Update`` / ``deploy-db`` — this does not
upload ``instance/*.db``.

Examples:
  python scripts/sync_vps_app_code.py --dry-run
  python scripts/sync_vps_app_code.py --restart
  python scripts/sync_vps_app_code.py --pip --restart
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RSYNC_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".env",
    ".env.local",
    "instance/*.db",
    "instance/*.db-wal",
    "instance/*.db-shm",
    "instance/league_json_cache/",
    "data/imports/raw/",
    "tests/",
    "backup-from-pa/",
    ".cursor/",
    "*.pyc",
)


def _find_rsync() -> str | None:
    found = shutil.which("rsync")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Git\usr\bin\rsync.exe"),
        Path(r"C:\Program Files (x86)\Git\usr\bin\rsync.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _ssh_key_path(env: dict[str, str]) -> str:
    key = (env.get("PA_SSH_KEY") or "").strip()
    if not key:
        default = Path.home() / ".ssh" / "id_ed25519_pa"
        if default.is_file():
            key = str(default)
    if not key or not Path(key).is_file():
        raise SystemExit("Set PA_SSH_KEY or place key at ~/.ssh/id_ed25519_pa")
    return key


def _sync_via_tar(env: dict[str, str], *, dry_run: bool) -> None:
    tar_exe = shutil.which("tar")
    if not tar_exe:
        raise SystemExit("Neither rsync nor tar found — install Git/WSL or enable Windows tar.")
    key = _ssh_key_path(env)
    from scripts.deploy_live_host import default_ssh_user

    user = default_ssh_user(env)
    host = env["PA_HOST"]
    remote = env["PA_REMOTE_PATH"].rstrip("/")
    remote_archive = "/tmp/bowl-code-sync.tar.gz"

    exclude_args: list[str] = []
    for pattern in RSYNC_EXCLUDES:
        exclude_args.extend(["--exclude", pattern.rstrip("/")])

    print("Using tar+scp fallback (no rsync on PATH).")
    if dry_run:
        print(f">>> would tar repo → scp → extract under {remote}/")
        return

    import tempfile

    fd, archive = tempfile.mkstemp(suffix=".tar.gz", prefix="bowl-sync-")
    os.close(fd)
    try:
        tar_cmd = [tar_exe, "-czf", archive, *exclude_args, "-C", str(REPO_ROOT), "."]
        _run(tar_cmd, dry_run=False)
        scp_cmd = [
            "scp",
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            archive,
            f"{user}@{host}:{remote_archive}",
        ]
        _run(scp_cmd, dry_run=False)
        _remote_shell(
            env,
            f"tar xzf {remote_archive} -C {remote} && rm -f {remote_archive}",
            dry_run=False,
        )
    finally:
        Path(archive).unlink(missing_ok=True)


def _ssh_rsh(env: dict[str, str]) -> str:
    key = _ssh_key_path(env)
    return f'ssh -i "{key}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new'


def _run(cmd: list[str], *, dry_run: bool) -> None:
    printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    print(f">>> {printable}")
    if dry_run:
        return
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def _remote_shell(env: dict[str, str], script: str, *, dry_run: bool) -> None:
    from scripts.deploy_live_host import default_ssh_user

    host = env["PA_HOST"]
    user = default_ssh_user(env)
    key = (env.get("PA_SSH_KEY") or "").strip() or str(Path.home() / ".ssh" / "id_ed25519_pa")
    cmd = ["ssh", "-i", key, "-o", "BatchMode=yes", f"{user}@{host}", script]
    _run(cmd, dry_run=dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rsync app code to the live VPS.")
    ap.add_argument("--dry-run", action="store_true", help="Print rsync command only.")
    ap.add_argument(
        "--pip",
        action="store_true",
        help="After sync, run remote pip install -r requirements.txt as bowl.",
    )
    ap.add_argument(
        "--restart",
        action="store_true",
        help="After sync (and optional pip), systemctl restart bowl-web.",
    )
    ap.add_argument(
        "--perfect-squad",
        action="store_true",
        help="Also rsync ../perfect-squad tree if PERFECT_SQUAD_REMOTE is set.",
    )
    args = ap.parse_args()

    from scripts.deploy_live_host import bootstrap_deploy_env, live_deploy_label, uses_vps_deploy

    env = bootstrap_deploy_env()
    if not uses_vps_deploy(env):
        print("BOWL_DEPLOY_TARGET is not VPS — refusing to sync.", file=sys.stderr)
        return 1

    remote_root = env["PA_REMOTE_PATH"].rstrip("/") + "/"
    rsync = _find_rsync()
    user = env.get("PA_USER") or "root"
    host = env["PA_HOST"]
    target = f"{user}@{host}:{remote_root}"

    print(f"Sync target: {live_deploy_label(env)} -> {remote_root}")

    if rsync:
        rsh = _ssh_rsh(env)
        cmd: list[str] = [
            rsync,
            "-avz",
            "--progress" if sys.stdout.isatty() else "--info=stats2",
            "-e",
            rsh,
        ]
        if args.dry_run:
            cmd.append("--dry-run")
        for pattern in RSYNC_EXCLUDES:
            cmd.extend(["--exclude", pattern])
        cmd.extend([f"{REPO_ROOT}/", target])
        _run(cmd, dry_run=args.dry_run)
    else:
        _sync_via_tar(env, dry_run=args.dry_run)

    if args.dry_run:
        if args.pip or args.restart:
            print("(dry-run: skipping remote chown / pip / restart)")
        return 0

    app = env["PA_REMOTE_PATH"].rstrip("/")
    _remote_shell(
        env,
        f"chown -R bowl:bowl {app}/app {app}/scripts {app}/deploy {app}/wsgi.py "
        f"{app}/requirements.txt 2>/dev/null || chown -R bowl:bowl {app}",
        dry_run=False,
    )

    if args.pip:
        venv = env.get("PA_REMOTE_VENV_BIN") or f"{app}/.venv/bin"
        _remote_shell(
            env,
            f"sudo -u bowl {venv}/pip install -q -r {app}/requirements.txt",
            dry_run=False,
        )

    if args.restart:
        _remote_shell(env, "systemctl restart bowl-web", dry_run=False)
        print("bowl-web restarted (bowl-cache-warm should run via ExecStartPost).")

    if args.perfect_squad:
        ps_remote = (env.get("PERFECT_SQUAD_REMOTE") or env.get("PERFECT_SQUAD_ROOT") or "").strip()
        if not ps_remote:
            ps_remote = str(Path(app).parent / "perfect-squad")
        ps_local = REPO_ROOT.parent / "perfect-squad"
        if ps_local.is_dir():
            if rsync:
                ps_cmd = [
                    rsync,
                    "-avz",
                    "-e",
                    _ssh_rsh(env),
                    "--exclude",
                    ".venv/",
                    "--exclude",
                    "__pycache__/",
                    f"{ps_local}/",
                    f"{user}@{host}:{ps_remote.rstrip('/')}/",
                ]
                _run(ps_cmd, dry_run=False)
            else:
                print("Skip perfect-squad tar sync (use rsync or sync PS separately).")
            _remote_shell(env, f"chown -R bowl:bowl {ps_remote}", dry_run=False)
        else:
            print(f"Skip perfect-squad: {ps_local} not found locally.")

    print("Code sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
