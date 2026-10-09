#!/usr/bin/env python3
"""Check Toronto Spaces + nyc3 DR bucket reachability (no secrets printed).

  python scripts/verify_spaces_backups.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "scripts" / "offsite-backup.env"


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _rclone() -> str:
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
    raise SystemExit("Install rclone: winget install Rclone.Rclone")


def _probe_bucket(rclone: str, conf: Path, bucket_remote: str, label: str) -> bool:
    p = subprocess.run(
        [rclone, "about", bucket_remote, "--config", str(conf)],
        capture_output=True,
        text=True,
    )
    err = (p.stderr or "") + (p.stdout or "")
    if "doesn't support about" in err or p.returncode == 0:
        print(f"OK  {label}: bucket exists")
        return True
    if "NoSuchBucket" in err:
        print(f"FAIL {label}: bucket missing at this endpoint (create in DO Spaces UI)")
    elif "AccessDenied" in err:
        print(f"FAIL {label}: access denied (check Spaces access key)")
    else:
        print(f"FAIL {label}: {err.strip()[:200]}")
    return False


def main() -> int:
    if not ENV_FILE.is_file():
        print(f"Missing {ENV_FILE}", file=sys.stderr)
        return 1

    env = _load_env(ENV_FILE)
    prefix = env.get("BOWL_SPACES_PREFIX", "bowl-production")
    primary_bucket = env["BOWL_SPACES_BUCKET"]
    sec_bucket = env.get("BOWL_SPACES_SECONDARY_BUCKET", "")
    sec_ep = env.get("BOWL_SPACES_SECONDARY_ENDPOINT", "")

    rclone = _rclone()
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
endpoint = {sec_ep or "nyc3.digitaloceanspaces.com"}
acl = private
""",
            encoding="utf-8",
        )

        ok_p = _probe_bucket(
            rclone,
            conf,
            f"bowl-spaces:{primary_bucket}",
            f"tor1 `{primary_bucket}`",
        )
        copy_prefix = env.get("BOWL_SPACES_COPY_PREFIX", "").strip()
        ok_s = False
        if sec_bucket and sec_ep:
            ok_s = _probe_bucket(
                rclone,
                conf,
                f"bowl-spaces-dr:{sec_bucket}",
                f"nyc3 `{sec_bucket}`",
            )
        elif copy_prefix:
            print(f"OK  DR strategy: intra-Space prefix `{copy_prefix}` (no NYC3)")
            ok_s = True

    if ok_p and ok_s:
        print("\nReady: mirror_spaces_backups_local.py, sync_spaces_intra_copy.py, VPS offsite timer.")
        return 0
    if ok_p and sec_bucket and sec_ep and not ok_s:
        print(
            "\nPrimary OK. Create NYC3 Space or unset SECONDARY_* and use BOWL_SPACES_COPY_PREFIX."
        )
        return 2
    return 1 if not ok_p else 0


if __name__ == "__main__":
    raise SystemExit(main())
