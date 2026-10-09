#!/usr/bin/env python3
"""Download Spaces backup mirror to this PC (belt-and-suspenders off-droplet copy).

Reads credentials from ``scripts/offsite-backup.env``. Requires rclone on PATH
(or Git for Windows: ``C:\\Program Files\\Git\\usr\\bin\\rsync.exe`` sibling).

  python scripts/mirror_spaces_backups_local.py
  python scripts/mirror_spaces_backups_local.py --include-secondary
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = REPO_ROOT / "scripts" / "offsite-backup.env"
DEFAULT_DEST = REPO_ROOT / "backup-from-do" / "spaces-mirror"


def _find_rclone() -> str:
    found = shutil.which("rclone")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Git\usr\bin\rclone.exe"),
        Path(r"C:\Program Files\rclone\rclone.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise SystemExit("Install rclone: winget install Rclone.Rclone (or Git for Windows).")


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _write_rclone_conf(env: dict[str, str], path: Path, *, include_secondary: bool) -> None:
    path.write_text(
        f"""[bowl-spaces]
type = s3
provider = DigitalOcean
access_key_id = {env["BOWL_SPACES_ACCESS_KEY"]}
secret_access_key = {env["BOWL_SPACES_SECRET_KEY"]}
endpoint = {env.get("BOWL_SPACES_ENDPOINT", "tor1.digitaloceanspaces.com")}
acl = private
"""
        + (
            f"""
[bowl-spaces-dr]
type = s3
provider = DigitalOcean
access_key_id = {env["BOWL_SPACES_ACCESS_KEY"]}
secret_access_key = {env["BOWL_SPACES_SECRET_KEY"]}
endpoint = {env["BOWL_SPACES_SECONDARY_ENDPOINT"]}
acl = private
"""
            if include_secondary
            and env.get("BOWL_SPACES_SECONDARY_ENDPOINT")
            and env.get("BOWL_SPACES_SECONDARY_BUCKET")
            else ""
        ),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument(
        "--include-secondary",
        action="store_true",
        help="Also mirror the secondary region bucket under dest/secondary/",
    )
    args = ap.parse_args()

    if not ENV_FILE.is_file():
        print(f"Missing {ENV_FILE}", file=sys.stderr)
        return 1

    env = _load_env(ENV_FILE)
    for key in ("BOWL_SPACES_ACCESS_KEY", "BOWL_SPACES_SECRET_KEY", "BOWL_SPACES_BUCKET"):
        if not env.get(key):
            print(f"Set {key} in {ENV_FILE}", file=sys.stderr)
            return 1

    prefix = env.get("BOWL_SPACES_PREFIX", "bowl-production")
    rclone = _find_rclone()
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "rclone.conf"
        _write_rclone_conf(env, conf, include_secondary=args.include_secondary)
        remote = f"bowl-spaces:{env['BOWL_SPACES_BUCKET']}/{prefix}/backups"
        cmd = [
            rclone,
            "sync",
            remote,
            str(dest / "primary"),
            "--config",
            str(conf),
            "--fast-list",
            "--transfers",
            "4",
        ]
        print(f">>> {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

        if args.include_secondary and env.get("BOWL_SPACES_SECONDARY_BUCKET"):
            remote2 = (
                f"bowl-spaces-dr:{env['BOWL_SPACES_SECONDARY_BUCKET']}/{prefix}/backups"
            )
            cmd2 = [
                rclone,
                "sync",
                remote2,
                str(dest / "secondary"),
                "--config",
                str(conf),
                "--fast-list",
                "--transfers",
                "4",
                "--s3-no-check-bucket",
            ]
            print(f">>> {' '.join(cmd2)}")
            sec = subprocess.run(cmd2)
            if sec.returncode != 0:
                print(
                    "Secondary mirror skipped (empty bucket or not created yet). "
                    "Run: python scripts/sync_spaces_to_secondary.py",
                    file=sys.stderr,
                )

    print(f"Spaces mirror updated under {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
