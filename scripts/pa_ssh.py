"""Shared Paramiko SFTP helpers for PythonAnywhere deploy scripts."""

from __future__ import annotations

import errno
import getpass
import os
import sys
import warnings
from pathlib import Path

import paramiko
from paramiko.ssh_exception import PasswordRequiredException


def _looks_like_private_key_file(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:500]
    except OSError:
        return False
    return (
        "BEGIN OPENSSH PRIVATE KEY" in head
        or "BEGIN RSA PRIVATE KEY" in head
        or "BEGIN EC PRIVATE KEY" in head
        or "BEGIN PRIVATE KEY" in head
    )


def _passphrase_from_env() -> str | None:
    """Optional: avoid interactive getpass (broken in some IDEs / terminals)."""
    for name in ("PA_SSH_PASSPHRASE", "SSH_KEY_PASSPHRASE"):
        v = os.environ.get(name)
        if v is not None and str(v).strip() != "":
            return str(v)
    return None


def _prompt_passphrase_gui(label: str) -> str | None:
    """Show a desktop dialog. Returns None if a dialog could not be opened.

    An empty string means the user submitted a blank passphrase. ``SystemExit``
    is raised if they cancel the dialog.
    """
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        value = simpledialog.askstring(
            "PythonAnywhere SSH",
            f"Passphrase for {label}:",
            show="*",
            parent=root,
        )
    except Exception:
        return None
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    if value is None:
        raise SystemExit("Passphrase entry cancelled.")
    return value


def _prompt_ssh_passphrase(label: str) -> str:
    """Ask for the key passphrase via a popup on Windows, else the terminal."""
    print(
        f"Enter the passphrase for {label} in the popup window "
        "(check the taskbar if you do not see it).",
        flush=True,
    )
    gui = _prompt_passphrase_gui(label)
    if gui is not None:
        return gui
    if not sys.stdin.isatty():
        raise SystemExit(
            "SSH key is encrypted and no passphrase dialog could be shown.\n"
            "Run from Cursor's terminal or PowerShell, or set PA_SSH_PASSPHRASE "
            "for this session only."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", getpass.GetPassWarning)
        return getpass.getpass(
            f"Passphrase for {label} (typing may not be hidden in this terminal): "
        )


def connect_sftp(
    host: str,
    user: str,
    key_path: Path | None,
) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    """
    Connect over SSH and open SFTP.

    Encrypted keys need a passphrase. In order, we use:

    1. Environment variable ``PA_SSH_PASSPHRASE`` or ``SSH_KEY_PASSPHRASE`` (same secret;
       avoid committing it).
    2. A desktop popup (tkinter) so Cursor/IDE terminals can still collect the passphrase.
    3. Otherwise ``getpass`` in a real terminal.

    To avoid passphrases entirely: use ``ssh-agent`` and ``ssh-add`` (then omit ``--key``),
    or generate a **new** deploy-only key **without** a passphrase and add its public key
    on PythonAnywhere (weaker if the PC is shared).
    """
    passphrase: str | None = _passphrase_from_env()
    max_passphrase_tries = 4
    tries = 0
    used_env_pass = passphrase is not None

    while True:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {
            "hostname": host,
            "port": 22,
            "username": user,
            "timeout": 30,
        }
        if key_path is not None:
            if not key_path.is_file():
                raise SystemExit(f"SSH key not found: {key_path}")
            if key_path.suffix.lower() == ".pub" or not _looks_like_private_key_file(
                key_path
            ):
                raise SystemExit(
                    f"Not a private key file: {key_path}\n"
                    "Use the private key (often id_ed25519 or id_rsa with NO .pub suffix), "
                    "not the .pub file."
                )
            kw["key_filename"] = str(key_path)
            kw["allow_agent"] = False
            kw["look_for_keys"] = False
        else:
            kw["allow_agent"] = True
            kw["look_for_keys"] = True
        if passphrase is not None:
            kw["passphrase"] = passphrase

        try:
            client.connect(**kw)
            transport = client.get_transport()
            if transport is not None:
                # Keep the TCP session alive during long SFTP gets/puts (editorial JSON, DBs).
                transport.set_keepalive(30)
            sftp = client.open_sftp()
            return client, sftp
        except PasswordRequiredException:
            tries += 1
            if tries > max_passphrase_tries:
                raise SystemExit("Too many passphrase attempts.") from None
            label = key_path.name if key_path else "default SSH key in ~/.ssh"
            passphrase = _prompt_ssh_passphrase(label)
            used_env_pass = False
        except paramiko.AuthenticationException as err:
            if passphrase is None:
                raise
            tries += 1
            if tries > max_passphrase_tries:
                raise SystemExit("SSH authentication failed.") from err
            if used_env_pass:
                raise SystemExit(
                    "SSH authentication failed. Check PA_SSH_PASSPHRASE / SSH_KEY_PASSPHRASE "
                    "matches this key, or remove the env var and enter the passphrase interactively."
                ) from err
            print("SSH auth failed (wrong passphrase?). Try again.", file=sys.stderr)
            passphrase = _prompt_ssh_passphrase(
                key_path.name if key_path else "SSH key"
            )
        except paramiko.SSHException as err:
            if "No authentication methods available" in str(err):
                if key_path is not None:
                    raise SystemExit(
                        "SSH: no authentication methods (private key not accepted).\n"
                        f"  Key file: {key_path}\n"
                        "  • Confirm this private key's public half is in PythonAnywhere "
                        "(Account → SSH keys).\n"
                        "  • If the key has a passphrase, set PA_SSH_PASSPHRASE for this session.\n"
                        '  • Test: ssh -i "PATH" BoiledEgg1974@ssh.pythonanywhere.com'
                    ) from err
                home_ssh = Path.home() / ".ssh"
                raise SystemExit(
                    "SSH: no authentication methods available.\n"
                    "Paramiko did not find a usable key. On Windows, either:\n"
                    f"  • Set PA_SSH_KEY to your private key file (not .pub), e.g.\n"
                    f'    set PA_SSH_KEY={home_ssh / "id_ed25519"}\n'
                    "  • Or start the OpenSSH Authentication Agent service, run "
                    "`ssh-add` with your key, then unset PA_SSH_KEY so the agent is used.\n"
                    f"  Expected keys are often under: {home_ssh}"
                ) from err
            raise


_SFTP_PROGRESS_MIN_BYTES = 512 * 1024
_SFTP_PROGRESS_STEP_BYTES = 4 * 1024 * 1024


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f" {n / 1024:.1f} KB".strip()
    return f"{n / (1024 * 1024):.1f} MB"


def _sftp_progress_cb(prefix: str, total: int):
    last = {"sent": 0}
    step = max(_SFTP_PROGRESS_STEP_BYTES, (total // 8) if total else 0)

    def cb(transferred: int, t2: int) -> None:
        tot = int(t2 or total or 0)
        cur = int(transferred)
        if tot <= 0:
            return
        if cur < tot and cur - last["sent"] < step:
            return
        last["sent"] = cur
        pct = min(100.0, 100.0 * cur / tot)
        print(f"{prefix}{_fmt_size(cur)} / {_fmt_size(tot)} ({pct:.0f}%)", flush=True)

    return cb


def sftp_get(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    local_path: str | Path,
    *,
    dest_label: str | None = None,
) -> int:
    """Download ``remote_path`` and print size/progress for large files. Returns byte size."""
    local = Path(local_path)
    local.parent.mkdir(parents=True, exist_ok=True)
    total = int(getattr(sftp.stat(remote_path), "st_size", 0) or 0)
    shown = dest_label or str(local)
    print(f"download {remote_path} -> {shown} ({_fmt_size(total)})", flush=True)
    cb = _sftp_progress_cb("  ", total) if total >= _SFTP_PROGRESS_MIN_BYTES else None
    sftp.get(remote_path, str(local), callback=cb)
    return total


def sftp_put(
    sftp: paramiko.SFTPClient,
    local_path: str | Path,
    remote_path: str,
    *,
    dest_label: str | None = None,
) -> int:
    """Upload ``local_path`` and print size/progress for large files. Returns byte size."""
    local = Path(local_path)
    total = int(local.stat().st_size) if local.is_file() else 0
    shown = dest_label or remote_path
    print(f"upload {shown} ({_fmt_size(total)})", flush=True)
    cb = _sftp_progress_cb("  ", total) if total >= _SFTP_PROGRESS_MIN_BYTES else None
    sftp.put(str(local), remote_path, callback=cb)
    return total


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    remote_dir = remote_dir.rstrip("/")
    if not remote_dir or remote_dir == "/":
        return
    parts = remote_dir.split("/")
    acc = ""
    for p in parts:
        if not p:
            continue
        acc += "/" + p
        try:
            sftp.stat(acc)
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOENT or e.args == (2, "No such file"):
                sftp.mkdir(acc)
            else:
                raise


def remote_mtime(sftp: paramiko.SFTPClient, remote_path: str) -> float | None:
    try:
        st = sftp.stat(remote_path)
        return float(st.st_mtime)
    except OSError as e:
        if getattr(e, "errno", None) == errno.ENOENT or e.args == (2, "No such file"):
            return None
        raise
