"""Live deploy host defaults (DigitalOcean VPS).

Production (bowlhockey.com) uses ``scripts/deploy-live-vps.env`` or the
``.example`` template.
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


def uses_vps_deploy(env: dict[str, str] | None = None) -> bool:
    """Production deploys always target the VPS."""
    return True


def apply_live_deploy_env(env: dict[str, str]) -> dict[str, str]:
    out = dict(env)
    for key, value in VPS_ENV_DEFAULTS.items():
        if key == "BOWL_DEPLOY_TARGET":
            continue
        if not (out.get(key) or "").strip():
            out[key] = value
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
    """Apply VPS overrides to STEP2 CLI connection fields."""
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
    host = (e.get("PA_HOST") or VPS_ENV_DEFAULTS["PA_HOST"]).strip()
    return f"VPS ({host})"


def default_ssh_user(env: dict[str, str] | None = None) -> str:
    e = bootstrap_deploy_env(env) if env is None else env
    return (e.get("PA_USER") or VPS_ENV_DEFAULTS["PA_USER"]).strip() or "root"
