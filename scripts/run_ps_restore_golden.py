"""Restore PA perfect-squad.db from uploaded golden copy and reload WSGI."""
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
    return Path.home() / "OneDrive" / "Desktop" / "BOWL Perfect Squad"


def main() -> None:
    local_ps = _default_local_ps_root()
    user = os.environ.get("PA_USER", "BoiledEgg1974")
    key_raw = os.environ.get("PA_SSH_KEY", "").strip()
    key_path = Path(key_raw) if key_raw else None
    ps = f"/home/{user}/bowl-perfect-squad"
    py = f"/home/{user}/venv/bin/python"

    client, sftp = connect_sftp(os.environ.get("PA_HOST", "ssh.pythonanywhere.com"), user, key_path)
    try:
        sftp_put(sftp, str(local_ps / "scripts/_check_exclusives.py"), f"{ps}/scripts/_check_exclusives.py")
        for rel in (
            "scripts/restore_golden_to_live_db.py",
            "app/services/migrate.py",
        ):
            sftp_put(sftp, str(local_ps / rel), f"{ps}/{rel}")
        golden = local_ps / "instance" / "perfect-squad.golden.db"
        if not golden.is_file():
            golden = local_ps / "instance" / "perfect-squad.db"
        if not golden.is_file():
            raise SystemExit(f"Missing golden DB under {local_ps / 'instance'}")
        print(f"Uploading golden DB from {golden.name}...")
        sftp_put(sftp, str(golden), f"{ps}/instance/perfect-squad.golden.db")
    finally:
        sftp.close()

    body = "; ".join(
        [
            f"cd {shlex.quote(ps)}",
            f"{py} scripts/restore_golden_to_live_db.py "
            "--source instance/perfect-squad.golden.db --target instance/perfect-squad.db --strip-owned",
            f"{py} scripts/_check_exclusives.py {ps}/instance/perfect-squad.db",
            "touch /var/www/www_bowlhockey_com_wsgi.py",
            "echo restored",
        ]
    )
    run_remote_bash(client, body)
    client.close()


if __name__ == "__main__":
    main()
