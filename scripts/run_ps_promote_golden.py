"""Upload local golden PS DB and promote Historical + Cap catalog on PythonAnywhere."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pa_ssh import connect_sftp, sftp_put
from STEP2_pythonanywhere import run_remote_bash


def _default_local_ps_root() -> Path:
    env = os.environ.get("PERFECT_SQUAD_LOCAL", "").strip()
    if env:
        return Path(env).expanduser()
    desktop = Path.home() / "OneDrive" / "Desktop" / "BOWL Perfect Squad"
    return desktop


def main() -> None:
    local_ps = _default_local_ps_root()
    from scripts.deploy_connection import ssh_host, ssh_user

    host = ssh_host()
    user = ssh_user()
    key_raw = os.environ.get("PA_SSH_KEY", "").strip()
    key_path = Path(key_raw) if key_raw else None
    from scripts.deploy_connection import remote_perfect_squad_root, remote_venv_bin

    ps = remote_perfect_squad_root()
    py = f"{remote_venv_bin()}/python"

    client, sftp = connect_sftp(host, user, key_path)
    try:
        for rel in (
            "scripts/promote_golden_leagues.py",
            "scripts/sync_catalog_from_source_db.py",
            "scripts/verify_catalog_sync.py",
        ):
            sftp_put(sftp, str(local_ps / rel), f"{ps}/{rel}")
        print("Uploading golden DB...")
        sftp_put(
            sftp,
            str(local_ps / "instance" / "perfect-squad.db"),
            f"{ps}/instance/perfect-squad.golden.db",
        )
    finally:
        sftp.close()

    from scripts.deploy_live_host import uses_vps_deploy
    from STEP2_pythonanywhere import web_reload_bash_fragments

    reload_parts = web_reload_bash_fragments("reload", ssh_user=user) if uses_vps_deploy() else [
        "touch /var/www/www_bowlhockey_com_wsgi.py 2>/dev/null || true"
    ]
    body = "; ".join(
        [
            f"cd {shlex.quote(ps)}",
            f"{py} scripts/promote_golden_leagues.py --repair-mission-rewards "
            "--source instance/perfect-squad.golden.db --target instance/perfect-squad.db "
            "--league bowl-historical --league bowl-cap",
            f"{py} scripts/verify_catalog_sync.py "
            "--source instance/perfect-squad.golden.db --target instance/perfect-squad.db "
            "--league bowl-historical --league bowl-cap || true",
            *reload_parts,
            "echo promoted",
        ]
    )
    print("Promoting golden Historical + Cap on live server...")
    run_remote_bash(client, body)
    client.close()


if __name__ == "__main__":
    main()
