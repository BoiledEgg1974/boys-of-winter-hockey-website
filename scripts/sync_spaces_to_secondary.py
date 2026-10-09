#!/usr/bin/env python3
"""One-shot: ensure DR bucket exists and mirror tor1 Spaces prefix -> nyc3.

Uses ``scripts/offsite-backup.env``. Safe to run from your PC when VPS SSH is flaky.

  python scripts/sync_spaces_to_secondary.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = REPO_ROOT / "scripts" / "offsite-backup.env"


def _find_rclone() -> str:
    found = shutil.which("rclone")
    if found:
        return found
    winget = Path(
        r"C:\Users\keeno\AppData\Local\Microsoft\WinGet\Packages"
        r"\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\rclone-v1.75.1-windows-amd64\rclone.exe"
    )
    if winget.is_file():
        return str(winget)
    raise SystemExit("Install rclone (winget install Rclone.Rclone).")


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main() -> int:
    if not ENV_FILE.is_file():
        print(f"Missing {ENV_FILE}", file=sys.stderr)
        return 1

    env = _load_env(ENV_FILE)
    for key in ("BOWL_SPACES_ACCESS_KEY", "BOWL_SPACES_SECRET_KEY", "BOWL_SPACES_BUCKET"):
        if not env.get(key):
            print(f"Set {key} in {ENV_FILE}", file=sys.stderr)
            return 1
    sec_ep = env.get("BOWL_SPACES_SECONDARY_ENDPOINT", "").strip()
    sec_bucket = env.get("BOWL_SPACES_SECONDARY_BUCKET", "").strip()
    if not sec_ep or not sec_bucket:
        print(
            "No NYC3 secondary configured. Use: python scripts/sync_spaces_intra_copy.py",
            file=sys.stderr,
        )
        return 1

    prefix = env.get("BOWL_SPACES_PREFIX", "bowl-production")
    rclone = _find_rclone()

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

[bowl-spaces-dr]
type = s3
provider = DigitalOcean
access_key_id = {env["BOWL_SPACES_ACCESS_KEY"]}
secret_access_key = {env["BOWL_SPACES_SECRET_KEY"]}
endpoint = {sec_ep}
acl = private
""",
            encoding="utf-8",
        )
        src = f"bowl-spaces:{env['BOWL_SPACES_BUCKET']}/{prefix}/backups"
        dst = f"bowl-spaces-dr:{sec_bucket}/{prefix}/backups"
        mkdir_cmd = [rclone, "mkdir", f"bowl-spaces-dr:{sec_bucket}", "--config", str(conf)]
        print(f">>> {' '.join(mkdir_cmd[:4])} ...")
        mk = subprocess.run(mkdir_cmd)
        if mk.returncode != 0:
            print(
                "Note: could not create DR bucket (create it in DO Spaces in nyc3, "
                f"name `{sec_bucket}`, then re-run). Continuing sync..."
            )

        sync_cmd = [
            rclone,
            "sync",
            src,
            dst,
            "--config",
            str(conf),
            "--fast-list",
            "--transfers",
            "4",
            "--checkers",
            "8",
            "--s3-no-check-bucket",
        ]
        print(f">>> {' '.join(sync_cmd[:4])} ...")
        subprocess.run(sync_cmd, check=True)

    print(f"Secondary mirror OK: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
