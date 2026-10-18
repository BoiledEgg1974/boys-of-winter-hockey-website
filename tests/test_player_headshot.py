"""Player headshot filenames ignore case, hyphens, and similar punctuation."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from app.services.player_headshot import (
    canonical_player_headshot_basename,
    headshot_match_key,
    resolve_player_headshot_static_filename,
    slug_name_part,
)


def _player(**kwargs: object) -> SimpleNamespace:
    data = {
        "first_name": "Wayne",
        "last_name": "Gretzky",
        "birth_date": date(1961, 1, 26),
        "headshot_path": None,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


class SlugNamePartTest(unittest.TestCase):
    def test_hyphens_apostrophes_periods_and_accents_become_ascii(self) -> None:
        self.assertEqual(slug_name_part("Jean-Pierre"), "jean_pierre")
        self.assertEqual(slug_name_part("O'Sullivan"), "o_sullivan")
        self.assertEqual(slug_name_part("B.J."), "b_j")
        self.assertEqual(slug_name_part("Pierre-Édouard"), "pierre_edouard")
        self.assertEqual(slug_name_part("Vojtěch"), "vojtech")
        self.assertEqual(slug_name_part("Růžička"), "ruzicka")
        self.assertEqual(slug_name_part("Öberg"), "oberg")

    def test_match_key_treats_hyphen_underscore_and_punctuation_as_equal(self) -> None:
        hyphen = headshot_match_key("Marc-Antoine_Pouliot_22_5_1985.png")
        underscore = headshot_match_key("Marc_Antoine_Pouliot_22_5_1985.png")
        self.assertEqual(hyphen, underscore)
        self.assertEqual(
            headshot_match_key("Patrick_O'Sullivan_1_2_1985.png"),
            headshot_match_key("Patrick_O_Sullivan_1_2_1985.png"),
        )
        self.assertEqual(
            headshot_match_key("B.J._Crombeen_10_7_1985.png"),
            headshot_match_key("BJ_Crombeen_10_7_1985.png"),
        )
        self.assertEqual(
            headshot_match_key("Pierre-Édouard_Bellemare_6_3_1985.png"),
            headshot_match_key("Pierre_Edouard_Bellemare_6_3_1985.png"),
        )


class CanonicalBasenameTest(unittest.TestCase):
    def test_canonical_name_is_lowercase_ascii(self) -> None:
        player = _player(
            first_name="Pierre-Édouard",
            last_name="Bellemare",
            birth_date=date(1985, 3, 6),
        )
        self.assertEqual(
            canonical_player_headshot_basename(player),
            "pierre_edouard_bellemare_6_3_1985.png",
        )


class ResolveHeadshotTest(unittest.TestCase):
    def _resolve(self, folder: Path, player: SimpleNamespace, rel: str = "players") -> str | None:
        return resolve_player_headshot_static_filename(folder, player, rel)

    def test_returns_on_disk_casing_not_canonical_lowercase(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            players = root / "players"
            players.mkdir()
            (players / "Wayne_Gretzky_26_1_1961.png").write_bytes(b"png")
            self.assertEqual(
                self._resolve(root, _player()),
                "players/Wayne_Gretzky_26_1_1961.png",
            )

    def test_hyphenated_file_matches_canonical_underscore_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            players = root / "players"
            players.mkdir()
            (players / "Jean-Pierre_Mallette_27_1_1943.png").write_bytes(b"png")
            player = _player(
                first_name="Jean-Pierre",
                last_name="Mallette",
                birth_date=date(1943, 1, 27),
            )
            self.assertEqual(
                self._resolve(root, player),
                "players/Jean-Pierre_Mallette_27_1_1943.png",
            )

    def test_apostrophe_and_accent_files_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            players = root / "players"
            players.mkdir()
            (players / "Patrick_O'Sullivan_1_2_1985.png").write_bytes(b"png")
            (players / "Vojtěch_Polák_27_6_1985.png").write_bytes(b"png")
            sullivan = _player(
                first_name="Patrick",
                last_name="O'Sullivan",
                birth_date=date(1985, 2, 1),
            )
            polak = _player(
                first_name="Vojtěch",
                last_name="Polák",
                birth_date=date(1985, 6, 27),
            )
            self.assertEqual(
                self._resolve(root, sullivan),
                "players/Patrick_O'Sullivan_1_2_1985.png",
            )
            self.assertEqual(
                self._resolve(root, polak),
                "players/Vojtěch_Polák_27_6_1985.png",
            )

    def test_prefers_portable_ascii_filename_when_both_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            players = root / "players"
            players.mkdir()
            (players / "Marc-Antoine_Pouliot_22_5_1985.png").write_bytes(b"png")
            (players / "Marc_Antoine_Pouliot_22_5_1985.png").write_bytes(b"png")
            player = _player(
                first_name="Marc-Antoine",
                last_name="Pouliot",
                birth_date=date(1985, 5, 22),
            )
            self.assertEqual(
                self._resolve(root, player),
                "players/Marc_Antoine_Pouliot_22_5_1985.png",
            )

    def test_shared_dir_falls_back_to_flat_players(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "players" / "shared").mkdir(parents=True)
            players = root / "players"
            (players / "BJ_Crombeen_10_7_1985.png").write_bytes(b"png")
            player = _player(
                first_name="B.J.",
                last_name="Crombeen",
                birth_date=date(1985, 7, 10),
            )
            self.assertEqual(
                self._resolve(root, player, "players/shared"),
                "players/BJ_Crombeen_10_7_1985.png",
            )


if __name__ == "__main__":
    unittest.main()
