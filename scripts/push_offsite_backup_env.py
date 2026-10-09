#!/usr/bin/env python3
"""Push local offsite Spaces credentials to the droplet and enable rclone sync.

Create ``scripts/offsite-backup.env`` from ``deploy/vps/offsite-backup.env.example``
(gitignored). Then:

  python scripts/push_offsite_backup_env.py
  python scripts/push_offsite_backup_env.py --test-sync
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOCAL_ENV = REPO_ROOT / "scripts" / "offsite-backup.env"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-sync", action="store_true", help="Run bowl-backup-offsite.service after install.")
    args = ap.parse_args()

    if not LOCAL_ENV.is_file():
        print(
            f"Create {LOCAL_ENV} from deploy/vps/offsite-backup.env.example (Spaces keys).",
            file=sys.stderr,
        )
        return 1

    from scripts.deploy_live_host import bootstrap_deploy_env, default_ssh_user, uses_vps_deploy

    env = bootstrap_deploy_env()
    if not uses_vps_deploy(env):
        print("Not configured for VPS.", file=sys.stderr)
        return 1

    key = (env.get("PA_SSH_KEY") or "").strip() or str(Path.home() / ".ssh" / "id_ed25519_pa")
    user = default_ssh_user(env)
    host = env["PA_HOST"]
    app = env["PA_REMOTE_PATH"].rstrip("/")

    scp = ["scp", "-i", key, "-o", "BatchMode=yes", str(LOCAL_ENV), f"{user}@{host}:/etc/bowl/offsite-backup.env"]
    print(f">>> {' '.join(scp)}")
    subprocess.run(scp, check=True)

    remote = (
        f"sed -i 's/\\r$//' /etc/bowl/offsite-backup.env && "
        f"chmod 600 /etc/bowl/offsite-backup.env && "
        f"bash {app}/deploy/vps/install-offsite-backup.sh"
    )
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", f"{user}@{host}", remote]
    print(f">>> {' '.join(ssh)}")
    subprocess.run(ssh, check=True)

    if args.test_sync:
        ssh2 = [
            "ssh",
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            f"{user}@{host}",
            "systemctl start bowl-backup-offsite.service; "
            "tail -15 /srv/bowl/backups/logs/offsite-*.log 2>/dev/null | tail -15",
        ]
        subprocess.run(ssh2, check=True)

    print("Offsite backup configured on VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
