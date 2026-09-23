"""Upload BOWL team logos + player art and PS action-shots to PythonAnywhere."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path, PurePosixPath

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pa_ssh import connect_sftp, ensure_remote_dir, sftp_put
from STEP2_pythonanywhere import run_remote_bash, wsgi_files_to_reload


def _upload_tree(sftp, local_dir: Path, remote_dir: str) -> int:
    if not local_dir.is_dir():
        print(f"skip missing {local_dir}")
        return 0
    n = 0
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir).as_posix()
        remote = f"{remote_dir.rstrip('/')}/{rel}"
        ensure_remote_dir(sftp, str(PurePosixPath(remote).parent))
        sftp_put(sftp, str(path), remote)
        n += 1
        if n % 100 == 0:
            print(f"  {n} files…")
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logos-only", action="store_true", help="Upload logos/teams only (exclusive cards)")
    ns = parser.parse_args()

    user = os.environ.get("PA_USER", "BoiledEgg1974")
    host = os.environ.get("PA_HOST", "ssh.pythonanywhere.com")
    key_raw = os.environ.get("PA_SSH_KEY", "").strip()
    key_path = Path(key_raw) if key_raw else None

    def _resolve_bowl_root() -> Path:
        candidates: list[Path] = []
        env = os.environ.get("BOWL_LOCAL", "").strip()
        if env:
            candidates.append(Path(env).expanduser())
        candidates.extend(
            [
                Path.home() / "Projects" / "Boys-Of-Winter-League",
                Path.home() / "OneDrive" / "Desktop" / "Boys-Of-Winter-League",
            ]
        )
        for root in candidates:
            if (root / "app" / "static" / "logos" / "teams").is_dir():
                return root.resolve()
        raise SystemExit(
            "Could not find BOWL repo (need app/static/logos/teams). Set BOWL_LOCAL to Boys-Of-Winter-League."
        )

    bowl = _resolve_bowl_root()
    ps = Path(os.environ.get("PERFECT_SQUAD_LOCAL", "")).expanduser()
    if not (ps / "app" / "__init__.py").is_file():
        ps = Path.home() / "OneDrive" / "Desktop" / "BOWL Perfect Squad"

    remote_bowl = f"/home/{user}/boys-of-winter-hockey-website"
    remote_ps = f"/home/{user}/bowl-perfect-squad"
    bowl_static = bowl / "app" / "static"

    client, sftp = connect_sftp(host, user, key_path)
    try:
        total = 0
        bowl_subs = ("logos/teams",) if ns.logos_only else ("logos/teams", "players/action", "players/headshots")
        for sub in bowl_subs:
            local = (bowl_static / sub).resolve()
            remote = f"{remote_bowl}/app/static/{sub}"
            print(f"Uploading BOWL {sub} from {local}…")
            total += _upload_tree(sftp, local, remote)
        if not ns.logos_only:
            shots = ps / "instance" / "action-shots"
            print("Uploading PS instance/action-shots…")
            total += _upload_tree(sftp, shots, f"{remote_ps}/instance/action-shots")
            ps_players = ps / "app" / "static" / "players"
            if ps_players.is_dir():
                print("Uploading PS app/static/players…")
                total += _upload_tree(sftp, ps_players, f"{remote_ps}/app/static/players")
        for css in (
            "living-core-exclusive-card.css",
            "historical-exclusive-card.css",
            "community-request-card.css",
        ):
            local_css = ps / "app" / "static" / "css" / css
            if local_css.is_file():
                remote_css = f"{remote_ps}/app/static/css/{css}"
                ensure_remote_dir(sftp, f"{remote_ps}/app/static/css")
                sftp_put(sftp, str(local_css), remote_css)
                total += 1
    finally:
        sftp.close()

    touch_paths = wsgi_files_to_reload(None, pa_user=user)
    if touch_paths:
        touch_cmd = " ".join(f"touch {p}" for p in touch_paths)
        run_remote_bash(client, f"{touch_cmd}; echo uploaded_{total}_media_files")
    else:
        run_remote_bash(client, f"echo uploaded_{total}_media_files")
    client.close()
    print(f"Done. {total} files uploaded.")


if __name__ == "__main__":
    main()
