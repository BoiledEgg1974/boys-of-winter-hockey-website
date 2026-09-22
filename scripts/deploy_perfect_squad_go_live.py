#!/usr/bin/env python3
"""Deploy Perfect Squad mount to PythonAnywhere and optional economy rebase.

Requires SSH access (``PA_SSH_KEY`` or agent). Does not print ``.env`` secrets.

Examples::

  python scripts/deploy_perfect_squad_go_live.py --code
  python scripts/deploy_perfect_squad_go_live.py --code --backup
  python scripts/deploy_perfect_squad_go_live.py --code --backup --apply-economy
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pa_ssh import connect_sftp, ensure_remote_dir, sftp_put  # noqa: E402
from STEP2_pythonanywhere import iter_local_files, run_remote_bash, wsgi_files_to_reload  # noqa: E402


def _default_local_ps_root() -> Path | None:
    env = os.environ.get("PERFECT_SQUAD_LOCAL", "").strip()
    if env:
        root = Path(env).expanduser()
        if (root / "app" / "__init__.py").is_file():
            return root
    desktop = Path.home() / "OneDrive" / "Desktop" / "BOWL Perfect Squad"
    if (desktop / "app" / "__init__.py").is_file():
        return desktop
    return None


def _upload_ps_tree(sftp, local_root: Path, remote_ps: str) -> int:
    from paramiko.sftp_client import SFTPClient

    assert isinstance(sftp, SFTPClient)
    local_root = local_root.resolve()
    remote_base = remote_ps.rstrip("/")
    uploaded = 0
    for local_path in iter_local_files(local_root, include_instance=False):
        rel = local_path.relative_to(local_root)
        remote_file = f"{remote_base}/{rel.as_posix()}"
        from pathlib import PurePosixPath

        ensure_remote_dir(sftp, str(PurePosixPath(remote_file).parent))
        sftp_put(sftp, str(local_path), remote_file)
        uploaded += 1
        if uploaded % 200 == 0:
            print(f"uploaded {uploaded} PS files…")
    return uploaded


def _tarball_extract_prep(user: str) -> str:
    ps_root = f"/home/{user}/bowl-perfect-squad"
    remote_tar = f"/home/{user}/bowl-ps-deploy.tgz"
    return "; ".join(
        [
            "set -euo pipefail",
            f"PS={shlex.quote(ps_root)}",
            f'TAR={shlex.quote(remote_tar)}',
            'OLD="${PS}.partial-$(date +%s)"',
            'if [ -d "$PS" ]; then mv "$PS" "$OLD"; fi',
            'mkdir -p "$PS"',
            'tar -xzf "$TAR" -C "$PS"',
            'rm -f "$TAR"',
            'rm -rf "$OLD" 2>/dev/null || true',
            "echo extracted_ps_tarball",
        ]
    )


def _build_ps_tarball(local_root: Path) -> Path:
    import subprocess
    import tempfile

    local_root = local_root.resolve()
    out = Path(tempfile.gettempdir()) / "bowl-ps-deploy.tgz"
    if out.is_file():
        out.unlink()
    cmd = [
        "tar",
        "-czf",
        str(out),
        "-C",
        str(local_root),
        "--exclude=.git",
        "--exclude=instance",
        "--exclude=.cursor",
        "--exclude=.pytest_cache",
        "--exclude=app/static/img/artifacts/_shared/scrap",
        ".",
    ]
    subprocess.run(cmd, check=True)
    return out


def _remote_script(
    *,
    apply_economy: bool,
    economy_dry_run: bool,
    backup: bool,
    ps_repo_url: str,
    upload_ps_from_local: bool,
    skip_bowl_git_sync: bool,
    ps_preinstalled: bool,
) -> str:
    user = os.environ.get("PA_USER", "BoiledEgg1974").strip() or "BoiledEgg1974"
    bowl = f"/home/{user}/boys-of-winter-hockey-website"
    ps_root = f"/home/{user}/bowl-perfect-squad"
    venv_bin = os.environ.get("PA_REMOTE_VENV_BIN", f"/home/{user}/venv/bin").rstrip("/")
    py = f"{venv_bin}/python"
    wsgi = os.environ.get("PA_WSGI_FILE", "/var/www/www_bowlhockey_com_wsgi.py")

    env_patch = r'''
import os
from pathlib import Path
bowl = Path(os.environ["BOWL_REPO"])
ps = Path(os.environ["PS_ROOT"])
env_path = bowl / ".env"
text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
lines = text.splitlines()
keys = {ln.split("=", 1)[0].strip(): ln for ln in lines if "=" in ln and not ln.strip().startswith("#")}

def setdefault(key: str, value: str) -> None:
    if key not in keys:
        lines.append(f"{key}={value}")

setdefault("PERFECT_SQUAD_ROOT", str(ps))
setdefault("PS_ECONOMY_LIVE", "1")
setdefault("AP_ECONOMY_MULTIPLIER", "10")
setdefault("PERFECT_SQUAD_DISABLED_LEAGUES", "bowl-fantasy")
ps_db = ps / "instance" / "perfect-squad.db"
ps_db.parent.mkdir(parents=True, exist_ok=True)
setdefault("PERFECT_SQUAD_DATABASE_URL", f"sqlite:///{ps_db.as_posix()}")
env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

ps_env = ps / ".env"
ps_lines = []
if ps_env.is_file():
    ps_lines = ps_env.read_text(encoding="utf-8").splitlines()
ps_keys = {ln.split("=", 1)[0].strip(): ln for ln in ps_lines if "=" in ln and not ln.strip().startswith("#")}

def ps_set(key: str, value: str) -> None:
    if key not in ps_keys:
        ps_lines.append(f"{key}={value}")

for key, val in keys.items():
    if key in ("SITE_DATABASE_URL", "SECRET_KEY", "SESSION_COOKIE_NAME"):
        ps_set(key, val.split("=", 1)[1])
ps_set("BOWL_ROOT", str(bowl))
ps_set("PS_ECONOMY_LIVE", "1")
ps_set("AP_ECONOMY_MULTIPLIER", "10")
ps_set("PERFECT_SQUAD_DATABASE_URL", f"sqlite:///{ps_db.as_posix()}")
ps_env.parent.mkdir(parents=True, exist_ok=True)
ps_env.write_text("\n".join(ps_lines).rstrip() + "\n", encoding="utf-8")
print("env patched (no secrets printed)")
'''

    parts = [
        "set -euo pipefail",
        f"BOWL={shlex.quote(bowl)}",
        f"PS={shlex.quote(ps_root)}",
        f"PY={shlex.quote(py)}",
    ]
    if not skip_bowl_git_sync:
        parts.extend(
            [
                f"cd {shlex.quote(bowl)}",
                "git stash push -m 'pre-perfect-squad-deploy' || true",
                "git fetch origin",
                "git checkout master",
                "git reset --hard origin/master",
            ]
        )
    parts.append(f"cd {shlex.quote(bowl)}")
    if not upload_ps_from_local and not ps_preinstalled:
        parts.extend(
            [
                f"if [ ! -d {shlex.quote(ps_root + '/.git')} ]; then "
                f"git clone {shlex.quote(ps_repo_url)} {shlex.quote(ps_root)}; "
                f"else cd {shlex.quote(ps_root)} && git fetch origin && git checkout main && git reset --hard origin/main; fi",
            ]
        )
    elif upload_ps_from_local and not ps_preinstalled:
        parts.append(f"mkdir -p {shlex.quote(ps_root)}")
    parts.extend(
        [
        f"cd {shlex.quote(bowl)}",
        f". {shlex.quote(venv_bin + '/activate')}",
        f"{shlex.quote(py)} -m pip install --upgrade -r {shlex.quote(bowl + '/requirements.txt')}",
        f"{shlex.quote(py)} -m pip install --upgrade -r {shlex.quote(ps_root + '/requirements.txt')}",
        f"export BOWL_REPO={shlex.quote(bowl)} PS_ROOT={shlex.quote(ps_root)}",
        f"{shlex.quote(py)} -c {shlex.quote(env_patch.strip())}",
        ]
    )

    if backup:
        parts.append(
            f"cd {shlex.quote(bowl)} && {shlex.quote(py)} scripts/backup_all_live_data.py "
            f"--out instance/full_backups/pre-perfect-squad-$(date +%Y%m%d-%H%M%S)"
        )

    ps_env = (
        f"cd {shlex.quote(ps_root)} && set -a && . {shlex.quote(bowl + '/.env')} && "
        f". {shlex.quote(ps_root + '/.env')} && set +a"
    )
    if apply_economy:
        parts.extend(
            [
                f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_economy.py",
                f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_economy.py --apply --confirm",
                f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_catalog.py --include-fantasy",
                f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_catalog.py --include-fantasy --apply --confirm",
            ]
        )
    elif economy_dry_run:
        parts.append(
            f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_economy.py && "
            f"{ps_env} && {shlex.quote(py)} scripts/rebase_ap_catalog.py --include-fantasy"
        )

    touch = [f"touch {shlex.quote(p)}" for p in wsgi_files_to_reload(wsgi, pa_user=user)]
    if touch:
        parts.append(" ".join(touch))
    parts.append(f"echo Done. Perfect Squad root: {ps_root}")
    return "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Perfect Squad to PythonAnywhere.")
    parser.add_argument("--code", action="store_true", help="Pull repos, pip install, patch env, reload WSGI.")
    parser.add_argument("--backup", action="store_true", help="Site backup before economy apply.")
    parser.add_argument(
        "--apply-economy",
        action="store_true",
        help="Apply AP balance + catalog rebase (requires --backup).",
    )
    parser.add_argument(
        "--ps-repo-url",
        default="https://github.com/BoiledEgg1974/bowl-perfect-squad.git",
    )
    parser.add_argument(
        "--local-ps-root",
        type=Path,
        default=None,
        help="Upload Perfect Squad tree via SFTP (default: Desktop clone or PERFECT_SQUAD_LOCAL).",
    )
    parser.add_argument(
        "--no-local-ps-upload",
        action="store_true",
        help="Use git clone on the server instead of SFTP upload.",
    )
    parser.add_argument(
        "--tarball",
        action="store_true",
        help="Upload one compressed archive (faster than per-file SFTP; excludes scrap images).",
    )
    parser.add_argument(
        "--economy-dry-run",
        action="store_true",
        help="After deploy, print AP rebase/catalog dry-run on the server (no writes).",
    )
    parser.add_argument(
        "--skip-bowl-git-sync",
        action="store_true",
        help="Do not hard-reset the BOWL repo on the server (use when STEP2 already deployed code).",
    )
    parser.add_argument("--host", default=os.environ.get("PA_HOST", "ssh.pythonanywhere.com"))
    parser.add_argument("--user", default=os.environ.get("PA_USER", "BoiledEgg1974"))
    ns = parser.parse_args()

    if not ns.code:
        parser.error("Pass --code to run deploy (add --backup --apply-economy for go-live rebase).")
    if ns.apply_economy and not ns.backup:
        parser.error("--apply-economy requires --backup first.")

    local_ps = ns.local_ps_root
    if local_ps is None and not ns.no_local_ps_upload:
        local_ps = _default_local_ps_root()
    upload_ps = local_ps is not None and not ns.no_local_ps_upload
    if upload_ps:
        print(f"Perfect Squad source (SFTP): {local_ps.resolve()}")
    ps_preinstalled = False
    script = _remote_script(
        apply_economy=ns.apply_economy,
        economy_dry_run=ns.economy_dry_run,
        backup=ns.backup,
        ps_repo_url=ns.ps_repo_url,
        upload_ps_from_local=upload_ps,
        skip_bowl_git_sync=ns.skip_bowl_git_sync,
        ps_preinstalled=ps_preinstalled,
    )
    user = ns.user.strip() or "BoiledEgg1974"
    remote_ps = f"/home/{user}/bowl-perfect-squad"
    remote_tar = f"/home/{user}/bowl-ps-deploy.tgz"
    client = None
    try:
        key_raw = os.environ.get("PA_SSH_KEY", "").strip()
        key_path = Path(key_raw) if key_raw else None
        client, sftp = connect_sftp(ns.host, ns.user, key_path)
        if upload_ps and local_ps is not None:
            if ns.tarball:
                print("--- building Perfect Squad tarball ---")
                tar_path = _build_ps_tarball(local_ps)
                print(f"Uploading {tar_path.name} ({tar_path.stat().st_size / (1024 * 1024):.1f} MB)…")
                sftp_put(sftp, str(tar_path), remote_tar)
                print("--- extracting Perfect Squad on server ---")
                run_remote_bash(client, _tarball_extract_prep(user))
            else:
                print("--- uploading Perfect Squad tree ---")
                n = _upload_ps_tree(sftp, local_ps, remote_ps)
                print(f"Uploaded {n} files to {remote_ps}")
            ps_preinstalled = True
            script = _remote_script(
                apply_economy=ns.apply_economy,
                economy_dry_run=ns.economy_dry_run,
                backup=ns.backup,
                ps_repo_url=ns.ps_repo_url,
                upload_ps_from_local=upload_ps,
                skip_bowl_git_sync=ns.skip_bowl_git_sync,
                ps_preinstalled=True,
            )
        print("--- remote Perfect Squad deploy ---")
        run_remote_bash(client, script)
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
