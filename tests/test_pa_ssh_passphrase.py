"""Passphrase prompt helpers for PythonAnywhere SSH."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _fake_tkinter(*, ask_value: str | None) -> MagicMock:
    tk = MagicMock()
    tk.TclError = type("TclError", (Exception,), {})
    fake_root = MagicMock()
    tk.Tk.return_value = fake_root
    tk.simpledialog.askstring.return_value = ask_value
    return tk


class PassphrasePromptTest(unittest.TestCase):
    def test_gui_cancel_exits(self) -> None:
        from scripts.pa_ssh import _prompt_passphrase_gui

        tk = _fake_tkinter(ask_value=None)
        with patch.dict("sys.modules", {"tkinter": tk, "tkinter.simpledialog": tk.simpledialog}):
            with self.assertRaises(SystemExit) as ctx:
                _prompt_passphrase_gui("id_ed25519_pa")
        self.assertIn("cancelled", str(ctx.exception).lower())

    def test_gui_submit_returns_value(self) -> None:
        from scripts.pa_ssh import _prompt_passphrase_gui

        tk = _fake_tkinter(ask_value="secret")
        with patch.dict("sys.modules", {"tkinter": tk, "tkinter.simpledialog": tk.simpledialog}):
            self.assertEqual(_prompt_passphrase_gui("id_ed25519_pa"), "secret")
