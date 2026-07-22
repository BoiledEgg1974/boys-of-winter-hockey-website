"""Fail-closed validation for deploy-db live capture JSON."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.STEP2_pythonanywhere import (
    _EDITORIAL_CAPTURE_LIST_KEYS,
    validate_deploy_capture_json,
)


class DeployCaptureValidationTests(unittest.TestCase):
    def test_missing_file_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            with self.assertRaisesRegex(ValueError, "bowl-cap editorial capture missing"):
                validate_deploy_capture_json(path, "editorial", slug="bowl-cap")

    def test_empty_file_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bowl-fantasy ovr capture empty"):
                validate_deploy_capture_json(path, "ovr", slug="bowl-fantasy")

    def test_editorial_without_version_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            payload = {k: [] for k in _EDITORIAL_CAPTURE_LIST_KEYS}
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing version"):
                validate_deploy_capture_json(path, "editorial", slug="bowl-historical")

    def test_valid_editorial_returns_total_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.json"
            payload = {k: [] for k in _EDITORIAL_CAPTURE_LIST_KEYS}
            payload["version"] = 1
            payload["hall_of_fame_members"] = [{"player_fhm_id": "1"}]
            payload["total_rows"] = 7
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            self.assertEqual(
                validate_deploy_capture_json(path, "editorial", slug="bowl-historical"),
                7,
            )

    def test_valid_ovr_and_list_captures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ovr = Path(tmp) / "ovr.json"
            ovr.write_text(json.dumps({"P1": 80}) + "\n", encoding="utf-8")
            self.assertEqual(validate_deploy_capture_json(ovr, "ovr", slug="bowl-cap"), 1)

            trades = Path(tmp) / "trades.json"
            trades.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                validate_deploy_capture_json(trades, "trade_log", slug="bowl-cap"),
                0,
            )


if __name__ == "__main__":
    unittest.main()
