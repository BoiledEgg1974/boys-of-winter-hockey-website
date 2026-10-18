"""FHM CSV encoding: Windows-1252 names, not cp1250/cp775 mojibake."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.import_pipeline.encoding_utils import (
    clean_fhm_text,
    decode_fhm_csv_bytes,
    read_csv_normalized,
)
from scripts.league_discord_bot.formatters import sanitize_discord_message_body


ROOT = Path(__file__).resolve().parents[1]


class DecodeFhmCsvBytesTest(unittest.TestCase):
    def test_utf8_roundtrip(self) -> None:
        text, enc = decode_fhm_csv_bytes("Åke;Öberg\n".encode("utf-8"))
        self.assertIn(enc, {"utf-8", "utf-8-sig"})
        self.assertIn("Åke", text)

    def test_cp1252_ake_and_rivieres(self) -> None:
        raw = "Åke;Trois-Rivières;Søren\n".encode("cp1252")
        text, enc = decode_fhm_csv_bytes(raw)
        self.assertEqual(enc, "cp1252")
        self.assertIn("Åke", text)
        self.assertIn("Trois-Rivières", text)
        self.assertIn("Søren", text)
        self.assertNotIn("Ĺke", text)
        self.assertNotIn("Rivičres", text)

    def test_drops_unused_cp1252_byte_in_juri(self) -> None:
        raw = b"J\x81\xfcri;Koitla"
        text, enc = decode_fhm_csv_bytes(raw)
        self.assertEqual(enc, "cp1252")
        self.assertEqual(text, "Jüri;Koitla")


class CleanFhmTextTest(unittest.TestCase):
    def test_repairs_cp1250_misread_nordic_and_french(self) -> None:
        self.assertEqual(clean_fhm_text("Ĺke"), "Åke")
        self.assertEqual(clean_fhm_text("Hĺkan"), "Håkan")
        self.assertEqual(clean_fhm_text("Dalsřren"), "Dalsøren")
        self.assertEqual(clean_fhm_text("Trois-Rivičres"), "Trois-Rivières")
        self.assertEqual(clean_fhm_text("Lindström"), "Lindström")


class ReadCsvNormalizedTest(unittest.TestCase):
    def test_historical_player_master_uses_windows_1252(self) -> None:
        path = ROOT / "data" / "imports" / "raw" / "bowl_historical" / "player_master.csv"
        if not path.is_file():
            self.skipTest("historical player_master.csv not in repo")
        df = read_csv_normalized(path)
        first = set(df["first_name"].astype(str))
        last = set(df["last_name"].astype(str))
        self.assertIn("Åke", first)
        self.assertNotIn("Ĺke", first)
        self.assertIn("Dalsøren", last)
        self.assertNotIn("Dalsřren", last)

    def test_fantasy_player_master_reads_umlauts(self) -> None:
        path = ROOT / "data" / "imports" / "raw" / "bowl_fantasy" / "player_master.csv"
        if not path.is_file():
            self.skipTest("fantasy player_master.csv not in repo")
        df = read_csv_normalized(path)
        last = set(df["last_name"].astype(str))
        self.assertIn("Bründl-Pandzich", last)
        self.assertNotIn("Br³ndl-Pandzich", last)


class DiscordSanitizeEncodingTest(unittest.TestCase):
    def test_repairs_mojibake_in_content_and_embeds(self) -> None:
        body = sanitize_discord_message_body(
            {
                "content": "Goal: Hĺkan Ĺke",
                "embeds": [{"title": "Trois-Rivičres Lions", "description": "Dalsřren"}],
            }
        )
        self.assertEqual(body["content"], "Goal: Håkan Åke")
        self.assertEqual(body["embeds"][0]["title"], "Trois-Rivières Lions")
        self.assertEqual(body["embeds"][0]["description"], "Dalsøren")


if __name__ == "__main__":
    unittest.main()
