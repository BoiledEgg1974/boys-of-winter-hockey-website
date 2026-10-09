#!/usr/bin/env python3
"""Copy Toronto Spaces backup prefix -> second prefix in the same bucket (no NYC3).

  python scripts/sync_spaces_intra_copy.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "scripts" / "offsite-backup.env"


def main() -> int:
    if not ENV_FILE.is_file():
        print(f"Missing {ENV_FILE}", file=sys.stderr)
        return 1

    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

    copy_prefix = env.get("BOWL_SPACES_COPY_PREFIX", "").strip()
    if not copy_prefix:
        print("Set BOWL_SPACES_COPY_PREFIX in offsite-backup.env (or use NYC3 secondary).", file=sys.stderr)
        return 1

    prefix = env.get("BOWL_SPACES_PREFIX", "bowl-production")
    bucket = env["BOWL_SPACES_BUCKET"]
    src = f"bowl-spaces:{bucket}/{prefix}/backups"
    dst = f"bowl-spaces:{bucket}/{copy_prefix}/backups"

    rclone = shutil.which("rclone")
    if not rclone:
        raise SystemExit("Install rclone: winget install Rclone.Rclone")

    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "rclone.conf"
        conf.write_text(
            f"""[bowl-spaces]
type = s3
provider = DigitalOcean
access_key_id = {env["BOWL_SPACES_ACCESS_KEY"]}
secret_access_key = {env["BOWL_SPACES_SECRET_KEY"]}
endpoint = {env.get("BOWL_SPACES_ENDPOINT", "tor1.digitaloceanspaces.com")}
acl = private
""",
            encoding="utf-8",
        )
        cmd = [
            rclone,
            "sync",
            src,
            dst,
            "--config",
            str(conf),
            "--fast-list",
            "--transfers",
            "4",
            "--s3-no-check-bucket",
        ]
        print(f">>> {' '.join(cmd[:4])} ...")
        subprocess.run(cmd, check=True)

    print(f"Intra-Space DR copy OK: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
