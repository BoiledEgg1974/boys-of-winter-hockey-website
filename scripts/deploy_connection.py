"""SSH target for live deploy scripts (defaults: DigitalOcean VPS)."""
from __future__ import annotations

from pathlib import Path

from scripts.deploy_live_host import bootstrap_deploy_env


def live_deploy_env() -> dict[str, str]:
    return bootstrap_deploy_env()


def ssh_host() -> str:
    return live_deploy_env()["PA_HOST"]


def ssh_user() -> str:
    return live_deploy_env()["PA_USER"]


def remote_app_root() -> str:
    return live_deploy_env()["PA_REMOTE_PATH"]


def remote_venv_bin() -> str:
    return live_deploy_env()["PA_REMOTE_VENV_BIN"]


def remote_perfect_squad_root() -> str:
    e = live_deploy_env()
    explicit = (e.get("PERFECT_SQUAD_REMOTE") or e.get("PERFECT_SQUAD_ROOT") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    app = Path(remote_app_root()).resolve()
    return str(app.parent / "perfect-squad")
