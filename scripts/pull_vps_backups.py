#!/usr/bin/env python3
"""Download /srv/bowl/backups from the live VPS to this PC (off-droplet copy).

Default destination: ``backup-from-do/vps-backups/`` (gitignored).

Examples:
  python scripts/pull_vps_backups.py
  python scripts/pull_vps_backups.py --dest D:\\Backups\\bowl-vps
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="SCP league backups from VPS to local disk.")
    ap.add_argument(
        "--dest",
        type=Path,
        default=REPO_ROOT / "backup-from-do" / "vps-backups",
        help="Local directory (created if missing).",
    )
    ap.add_argument(
        "--remote-dir",
        default="/srv/bowl/backups",
        help="Backup root on the VPS.",
    )
    args = ap.parse_args()

    from scripts.deploy_live_host import bootstrap_deploy_env, default_ssh_user, uses_vps_deploy

    env = bootstrap_deploy_env()
    if not uses_vps_deploy(env):
        print("Not configured for VPS deploy.", file=sys.stderr)
        return 1

    key = (env.get("PA_SSH_KEY") or "").strip() or str(Path.home() / ".ssh" / "id_ed25519_pa")
    user = default_ssh_user(env)
    host = env["PA_HOST"]
    remote = args.remote_dir.rstrip("/")
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    # scp -r user@host:/srv/bowl/backups/* dest/
    # Trailing "/." copies directory contents, not the directory name (Windows scp).
    src = f"{user}@{host}:{remote}/."
    cmd = ["scp", "-i", key, "-o", "BatchMode=yes", "-r", src, str(dest)]
    print(f">>> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"Backups copied under {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
