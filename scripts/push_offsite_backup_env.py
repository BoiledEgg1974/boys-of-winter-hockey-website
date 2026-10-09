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

    vps_deploy = REPO_ROOT / "deploy" / "vps"
    ssh_base = [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=60",
        "-o",
        "ServerAliveInterval=15",
    ]
    bundle = [
        LOCAL_ENV,
        vps_deploy / "install-offsite-backup.sh",
        vps_deploy / "sync-backups-offsite.sh",
        vps_deploy / "sync-backups-offsite-secondary.sh",
        vps_deploy / "bowl-backup-offsite.service",
        vps_deploy / "bowl-backup-offsite-secondary.service",
        vps_deploy / "bowl-backup-offsite.timer",
    ]
    # One tar stream over SSH (fewer connections than many scp calls).
    remote_unpack = (
        f"set -euo pipefail; tmp=$(mktemp -d); tar -xzf - -C \"$tmp\"; "
        f"install -m 600 \"$tmp/offsite-backup.env\" /etc/bowl/offsite-backup.env; "
        f"install -m 755 \"$tmp/install-offsite-backup.sh\" {app}/deploy/vps/install-offsite-backup.sh; "
        f"install -m 755 \"$tmp/sync-backups-offsite.sh\" {app}/deploy/vps/sync-backups-offsite.sh; "
        f"install -m 755 \"$tmp/sync-backups-offsite-secondary.sh\" {app}/deploy/vps/sync-backups-offsite-secondary.sh; "
        f"install -m 644 \"$tmp/bowl-backup-offsite.service\" {app}/deploy/vps/bowl-backup-offsite.service; "
        f"install -m 644 \"$tmp/bowl-backup-offsite-secondary.service\" {app}/deploy/vps/bowl-backup-offsite-secondary.service; "
        f"install -m 644 \"$tmp/bowl-backup-offsite.timer\" {app}/deploy/vps/bowl-backup-offsite.timer; "
        f"rm -rf \"$tmp\""
    )
    import io
    import tarfile

    buf = io.BytesIO()
    names = {
        LOCAL_ENV.name: "offsite-backup.env",
        "install-offsite-backup.sh": "install-offsite-backup.sh",
        "sync-backups-offsite.sh": "sync-backups-offsite.sh",
        "sync-backups-offsite-secondary.sh": "sync-backups-offsite-secondary.sh",
        "bowl-backup-offsite.service": "bowl-backup-offsite.service",
        "bowl-backup-offsite-secondary.service": "bowl-backup-offsite-secondary.service",
        "bowl-backup-offsite.timer": "bowl-backup-offsite.timer",
    }
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in bundle:
            arc = names.get(path.name, path.name)
            tar.add(path, arcname=arc)
    payload = buf.getvalue()
    print(f">>> ssh+tar upload ({len(payload)} bytes) -> {host}")
    proc = subprocess.run(
        [*ssh_base, f"{user}@{host}", remote_unpack],
        input=payload,
        check=False,
    )
    if proc.returncode != 0:
        return proc.returncode

    remote = (
        f"sed -i 's/\\r$//' /etc/bowl/offsite-backup.env && "
        f"chmod 600 /etc/bowl/offsite-backup.env && "
        f"sed -i 's/\\r$//' {app}/deploy/vps/*.sh && "
        f"bash {app}/deploy/vps/install-offsite-backup.sh"
    )
    ssh = [*ssh_base, f"{user}@{host}", remote]
    print(f">>> {' '.join(ssh)}")
    subprocess.run(ssh, check=True)

    if args.test_sync:
        ssh2 = [
            *ssh_base,
            f"{user}@{host}",
            "systemctl start bowl-backup-offsite.service; "
            "systemctl start bowl-backup-offsite-secondary.service; "
            "tail -20 /srv/bowl/backups/logs/offsite*.log 2>/dev/null | tail -20",
        ]
        subprocess.run(ssh2, check=True)

    print("Offsite backup configured on VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
