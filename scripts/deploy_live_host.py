"""Live deploy host defaults (DigitalOcean VPS vs legacy PythonAnywhere).

Production (bowlhockey.com on DO) uses ``scripts/deploy-live-vps.env`` or the
``.example`` template. Set ``BOWL_DEPLOY_TARGET=pa`` to push to PythonAnywhere again.
"""
from __future__ import annotations

import os
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent

VPS_ENV_DEFAULTS: dict[str, str] = {
    "BOWL_DEPLOY_TARGET": "vps",
    "PA_HOST": "159.203.6.136",
    "PA_USER": "root",
    "PA_REMOTE_PATH": "/srv/bowl/app",
    "PA_REMOTE_VENV_BIN": "/srv/bowl/app/.venv/bin",
    "BOWL_WEB_RELOAD": "systemd",
}

PA_LEGACY_DEFAULTS: dict[str, str] = {
    "BOWL_DEPLOY_TARGET": "pa",
    "PA_HOST": "ssh.pythonanywhere.com",
    "PA_USER": "BoiledEgg1974",
    "PA_REMOTE_PATH": "/home/BoiledEgg1974/boys-of-winter-hockey-website",
    "PA_REMOTE_VENV_BIN": "/home/BoiledEgg1974/venv/bin",
    "BOWL_WEB_RELOAD": "wsgi",
    "PA_WSGI_FILE": "/var/www/www_bowlhockey_com_wsgi.py",
}

DEPLOY_ENV_FILES: tuple[Path, ...] = (
    _SCRIPTS_DIR / "deploy-live-vps.env.example",
    _SCRIPTS_DIR / "deploy-live-vps.env",
)


def parse_deploy_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"')
    return out


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
    if (out.get("BOWL_DEPLOY_TARGET") or "").strip().lower() in ("pa", "pythonanywhere"):
        for key, value in PA_LEGACY_DEFAULTS.items():
            if key == "BOWL_DEPLOY_TARGET":
                continue
            if not (out.get(key) or "").strip():
                out[key] = value
        out.setdefault("BOWL_DEPLOY_TARGET", "pa")
        return out

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


def bootstrap_deploy_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Merge deploy env files, VPS defaults, and the caller environment."""
    out = dict(environ if environ is not None else os.environ)
    for path in DEPLOY_ENV_FILES:
        if not path.is_file():
            continue
        parsed = parse_deploy_env_file(path)
        if path.name.endswith(".example"):
            for key, value in parsed.items():
                if not (out.get(key) or "").strip():
                    out[key] = value
        else:
            out.update(parsed)
    return apply_live_deploy_env(out)


def resolve_step2_connection(
    *,
    host: str,
    user: str,
    remote_path: str,
    venv_bin: str,
    environ: dict[str, str] | None = None,
) -> tuple[str, str, str, str]:
    """Apply VPS/PA overrides to STEP2 CLI connection fields."""
    base = bootstrap_deploy_env(environ)
    base["PA_HOST"] = host
    base["PA_USER"] = user
    base["PA_REMOTE_PATH"] = remote_path
    base["PA_REMOTE_VENV_BIN"] = venv_bin
    merged = apply_live_deploy_env(base)
    return (
        merged.get("PA_HOST") or host,
        merged.get("PA_USER") or user,
        merged.get("PA_REMOTE_PATH") or remote_path,
        merged.get("PA_REMOTE_VENV_BIN") or venv_bin,
    )


def live_deploy_label(env: dict[str, str] | None = None) -> str:
    e = bootstrap_deploy_env(env) if env is None else env
    if uses_vps_deploy(e):
        host = (e.get("PA_HOST") or VPS_ENV_DEFAULTS["PA_HOST"]).strip()
        return f"VPS ({host})"
    return "PythonAnywhere"


def default_ssh_user(env: dict[str, str] | None = None) -> str:
    e = bootstrap_deploy_env(env) if env is None else env
    return (e.get("PA_USER") or VPS_ENV_DEFAULTS["PA_USER"]).strip() or "root"
