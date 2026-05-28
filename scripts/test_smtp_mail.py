#!/usr/bin/env python3
"""Test Gmail/SMTP login using .env in the repo root (run on the server console).

Usage:
    python scripts/test_smtp_mail.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import Config
from app.mail_util import smtp_config_diagnostics, test_smtp_login


def main() -> int:
    app = create_app(Config)
    with app.app_context():
        diag = smtp_config_diagnostics()
        print("SMTP configuration loaded from .env:")
        for key in (
            "host",
            "port",
            "username",
            "from_addr",
            "password_length",
            "password_length_ok",
            "tls_mode",
            "recipient",
        ):
            print(f"  {key}: {diag.get(key)}")
        print()
        result = test_smtp_login()
        if result.get("ok"):
            print("SMTP login: OK")
            return 0
        print("SMTP login: FAILED")
        print(f"  error: {result.get('error')}")
        if result.get("hint"):
            print(f"  hint: {result.get('hint')}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
