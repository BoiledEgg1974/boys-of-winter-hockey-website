from pathlib import Path

from app.services.import_validation import _read_csv_rows, _read_csv_rows_autodelim


def test_read_csv_rows_falls_back_to_cp1252(tmp_path: Path):
    csv_path = tmp_path / "players.csv"
    # é in cp1252 (0xE9) is invalid as a UTF-8 continuation byte.
    csv_path.write_bytes(b"name,team\nJos\xe9,MTL\n")

    rows = _read_csv_rows(csv_path)

    assert len(rows) == 1
    assert rows[0]["name"] == "José"
    assert rows[0]["team"] == "MTL"


def test_read_csv_rows_autodelim_falls_back_to_latin1(tmp_path: Path):
    csv_path = tmp_path / "team_identity_history.csv"
    csv_path.write_bytes(b"team_name;logo_file\nMontr\xe9al;logos/mtl.png\n")

    rows = _read_csv_rows_autodelim(csv_path)

    assert len(rows) == 1
    assert rows[0]["team_name"] == "Montréal"
    assert rows[0]["logo_file"] == "logos/mtl.png"
