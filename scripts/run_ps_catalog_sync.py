"""Upload local golden PS SQLite and sync card art + missions on PythonAnywhere."""
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
    host = os.environ.get("PA_HOST", "ssh.pythonanywhere.com")
    user = os.environ.get("PA_USER", "BoiledEgg1974")
    key_raw = os.environ.get("PA_SSH_KEY", "").strip()
    key_path = Path(key_raw) if key_raw else None
    ps = f"/home/{user}/bowl-perfect-squad"

    client, sftp = connect_sftp(host, user, key_path)
    try:
        print("Uploading sync script...")
        sftp_put(
            sftp,
            str(local_ps / "scripts" / "sync_catalog_from_source_db.py"),
            f"{ps}/scripts/sync_catalog_from_source_db.py",
        )
        print("Uploading golden DB...")
        sftp_put(
            sftp,
            str(local_ps / "instance" / "perfect-squad.db"),
            f"{ps}/instance/perfect-squad.golden.db",
        )
    finally:
        sftp.close()

    py = f"/home/{user}/venv/bin/python"
    body = "; ".join(
        [
            f"cd {shlex.quote(ps)}",
            f"{py} scripts/sync_catalog_from_source_db.py --repair-mission-rewards "
            "--source instance/perfect-squad.golden.db --target instance/perfect-squad.db "
            "--league bowl-historical --league bowl-cap",
            "touch /var/www/www_bowlhockey_com_wsgi.py",
            "echo wsgi_touched",
        ]
    )
    print("Running sync on PA...")
    run_remote_bash(client, body)
    client.close()


if __name__ == "__main__":
    main()
