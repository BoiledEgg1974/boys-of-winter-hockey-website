"""Live deploy host defaults (PythonAnywhere vs VPS).

Set ``BOWL_DEPLOY_TARGET=vps`` (or point ``PA_HOST`` at your droplet) before
``BOWL-Site-Update.py`` / ``STEP2_pythonanywhere.py`` so deploy-db uses systemd
instead of touching WSGI on PythonAnywhere.
"""
from __future__ import annotations

import os

VPS_ENV_DEFAULTS: dict[str, str] = {
    "BOWL_DEPLOY_TARGET": "vps",
    "PA_HOST": "159.203.6.136",
    "PA_USER": "root",
    "PA_REMOTE_PATH": "/srv/bowl/app",
    "PA_REMOTE_VENV_BIN": "/srv/bowl/app/.venv/bin",
    "BOWL_WEB_RELOAD": "systemd",
}


def _host_implies_vps(host: str) -> bool:
    h = (host or "").strip().lower()
    if not h or h in ("ssh.pythonanywhere.com",):
        return False
    return "pythonanywhere" not in h


def uses_vps_deploy(env: dict[str, str] | None = None) -> bool:
    e = env if env is not None else os.environ
    target = (e.get("BOWL_DEPLOY_TARGET") or e.get("BOWL_DEPLOY") or "").strip().lower()
    if target in ("vps", "do", "digitalocean"):
        return True
    if target in ("pa", "pythonanywhere"):
        return False
    return _host_implies_vps(str(e.get("PA_HOST") or ""))


def apply_live_deploy_env(env: dict[str, str]) -> dict[str, str]:
    out = dict(env)
    if not uses_vps_deploy(out):
        return out
    for key, value in VPS_ENV_DEFAULTS.items():
        if key == "BOWL_DEPLOY_TARGET":
            continue
        if not (out.get(key) or "").strip():
            out[key] = value
    host = (out.get("PA_HOST") or "").strip().lower()
    if "pythonanywhere" in host:
        out["PA_HOST"] = VPS_ENV_DEFAULTS["PA_HOST"]
    path = (out.get("PA_REMOTE_PATH") or "").strip()
    if path.startswith("/home/BoiledEgg1974/"):
        out["PA_REMOTE_PATH"] = VPS_ENV_DEFAULTS["PA_REMOTE_PATH"]
    venv = (out.get("PA_REMOTE_VENV_BIN") or "").strip()
    if venv.startswith("/home/BoiledEgg1974/"):
        out["PA_REMOTE_VENV_BIN"] = VPS_ENV_DEFAULTS["PA_REMOTE_VENV_BIN"]
    user = (out.get("PA_USER") or "").strip()
    if user == "BoiledEgg1974":
        out["PA_USER"] = VPS_ENV_DEFAULTS["PA_USER"]
    out.setdefault("BOWL_WEB_RELOAD", "systemd")
    out.setdefault("BOWL_DEPLOY_TARGET", "vps")
    return out


def resolve_step2_connection(
    *,
    host: str,
    user: str,
    remote_path: str,
    venv_bin: str,
) -> tuple[str, str, str, str]:
    """Apply VPS/PA overrides to STEP2 CLI connection fields."""
    merged = apply_live_deploy_env(
        {
            "PA_HOST": host,
            "PA_USER": user,
            "PA_REMOTE_PATH": remote_path,
            "PA_REMOTE_VENV_BIN": venv_bin,
            "BOWL_DEPLOY_TARGET": os.environ.get("BOWL_DEPLOY_TARGET", ""),
            "BOWL_WEB_RELOAD": os.environ.get("BOWL_WEB_RELOAD", ""),
        }
    )
    return (
        merged.get("PA_HOST") or host,
        merged.get("PA_USER") or user,
        merged.get("PA_REMOTE_PATH") or remote_path,
        merged.get("PA_REMOTE_VENV_BIN") or venv_bin,
    )


def live_deploy_label(env: dict[str, str] | None = None) -> str:
    e = env if env is not None else os.environ
    if uses_vps_deploy(e):
        host = (e.get("PA_HOST") or VPS_ENV_DEFAULTS["PA_HOST"]).strip()
        return f"VPS ({host})"
    return "PythonAnywhere"
