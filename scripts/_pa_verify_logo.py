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
rel = "app/static/logos/teams/bowl_historical/los_angeles_kings_1967-1974.png"
bowl = f"/home/{user}/boys-of-winter-hockey-website/{rel}"
ps_css = f"/home/{user}/bowl-perfect-squad/app/static/css/historical-exclusive-card.css"
cmd = f"""
test -f {bowl} && wc -c {bowl} || echo MISSING_BOWL_LOGO
test -f {ps_css} && wc -c {ps_css} || echo MISSING_PS_CSS
test -f /home/{user}/bowl-perfect-squad/app/templates/_card_historical_exclusive.html && echo HAS_HIST_TEMPLATE || echo NO_HIST_TEMPLATE
PY=/home/{user}/venv/bin/python
$PY /home/{user}/bowl-perfect-squad/scripts/_check_exclusives.py /home/{user}/bowl-perfect-squad/instance/perfect-squad.db 2>/dev/null || true
"""
run_remote_bash(client, cmd)
client.close()
