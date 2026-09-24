from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
from pa_ssh import connect_sftp
from STEP2_pythonanywhere import run_remote_bash

user = os.environ.get("PA_USER", "BoiledEgg1974")
key = Path(os.environ.get("PA_SSH_KEY", ""))
client, _ = connect_sftp("ssh.pythonanywhere.com", user, key if key.is_file() else None)
py = f"/home/{user}/venv/bin/python"
bowl = f"/home/{user}/boys-of-winter-hockey-website"
ps = f"/home/{user}/bowl-perfect-squad"
rel = "logos/teams/bowl_historical/los_angeles_kings_1967-1974.png"
run_remote_bash(
    client,
    f"test -f {bowl}/app/static/{rel} && echo FILE_OK || echo FILE_MISSING",
)
run_remote_bash(
    client,
    f"curl -sI 'https://www.bowlhockey.com/bowl-historical/perfect-squad/bowl-historical/bowl-media/{rel}' | head -8",
)
client.close()
