"""Convert wide-format BOWL All-Stars spreadsheet rows into ``history_all_stars.csv``.

Reads repeating blocks: season header row, First Team (6 lines), Second Team (6 lines),
stride 4 columns per season. Resolves ``player_id`` / ``team_id`` from ``player_master.csv``.

Example:

  python scripts/convert_wide_all_stars_to_history_csv.py \\
    --wide data/imports/raw/bowl_cap/wide_all_stars_source.csv \\
    --out data/imports/raw/bowl_cap/history_all_stars.csv \\
    --player-master data/imports/raw/bowl_cap/player_master.csv
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

SEASON_HDR = re.compile(r"BOWL\s*-\s*(\d{4}-\d{2})\s*SEASON", re.I)
STRIDE = 4


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().split())


def _load_player_lookup(master_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Maps normalized ``first last`` -> PlayerId, and PlayerId -> TeamId (last wins)."""
    df = read_csv_normalized(master_path)
    name_to_pid: dict[str, str] = {}
    pid_to_tid: dict[str, str] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = cell_val(r, "player_id", "playerid")
        fn = cell_val(r, "first_name", "firstname", "fname")
        ln = cell_val(r, "last_name", "lastname", "lname")
        tid = cell_val(r, "team_id", "teamid")
        if not pid or not fn or not ln:
            continue
        key = _norm_name(f"{fn} {ln}")
        if key not in name_to_pid:
            name_to_pid[key] = str(pid).strip()
        if tid:
            pid_to_tid[str(pid).strip()] = str(tid).strip()
    return name_to_pid, pid_to_tid


def _rows_from_wide(wide_path: Path) -> list[list[str]]:
    text = wide_path.read_text(encoding="utf-8", errors="replace")
    return list(csv.reader(text.splitlines()))


def convert_wide_to_long(
    wide_rows: list[list[str]],
    name_to_pid: dict[str, str],
    pid_to_tid: dict[str, str],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, int, int]] = set()
    i = 0
    while i < len(wide_rows):
        row = wide_rows[i]
        if not row or all(not (c or "").strip() for c in row):
            i += 1
            continue
        seasons: list[tuple[int, str]] = []
        for c in range(0, len(row), STRIDE):
            cell = (row[c] if c < len(row) else "") or ""
            m = SEASON_HDR.match(cell.strip())
            if m:
                label = m.group(1)
                seasons.append((c, label))
        if not seasons:
            i += 1
            continue
        i += 1
        if i >= len(wide_rows):
            break
        i += 1  # First Team label row
        if i >= len(wide_rows):
            break
        i += 1  # Position / Team / Player header
        first_block: list[list[str]] = []
        for _ in range(6):
            if i < len(wide_rows):
                first_block.append(wide_rows[i])
            i += 1
        if i < len(wide_rows):
            i += 1  # Second Team label
        if i < len(wide_rows):
            i += 1  # header
        second_block: list[list[str]] = []
        for _ in range(6):
            if i < len(wide_rows):
                second_block.append(wide_rows[i])
            i += 1

        def emit(team_rank: int, block: list[list[str]]) -> None:
            for slot_idx, data_row in enumerate(block):
                slot = 1 + slot_idx
                for col_off, season_label in seasons:
                    pos = (data_row[col_off] if col_off < len(data_row) else "") or ""
                    player_name = (data_row[col_off + 2] if col_off + 2 < len(data_row) else "") or ""
                    pos = pos.strip()
                    player_name = player_name.strip()
                    if not player_name:
                        continue
                    key = (season_label, team_rank, slot)
                    if key in seen:
                        continue
                    seen.add(key)
                    nk = _norm_name(player_name)
                    pid = name_to_pid.get(nk)
                    notes_parts: list[str] = []
                    if not pid:
                        notes_parts.append(f"unresolved_player={player_name}")
                    tid = ""
                    if pid:
                        tid = pid_to_tid.get(str(pid).strip(), "") or ""
                    row_out = {
                        "season": season_label,
                        "team": str(team_rank),
                        "slot": str(slot),
                        "position": pos or "?",
                        "player_id": str(pid) if pid else "",
                        "team_id": tid,
                        "notes": "; ".join(notes_parts) if notes_parts else "",
                    }
                    out.append(row_out)

        emit(1, first_block)
        emit(2, second_block)

    out.sort(key=lambda r: (r["season"], int(r["team"]), int(r["slot"])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wide", type=Path, required=True, help="Wide-format All-Stars CSV.")
    ap.add_argument("--out", type=Path, required=True, help="Output history_all_stars.csv path.")
    ap.add_argument(
        "--player-master",
        type=Path,
        required=True,
        help="player_master.csv for name → PlayerId / TeamId.",
    )
    args = ap.parse_args()
    name_to_pid, pid_to_tid = _load_player_lookup(args.player_master.resolve())
    wide_rows = _rows_from_wide(args.wide.resolve())
    rows = convert_wide_to_long(wide_rows, name_to_pid, pid_to_tid)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["season", "team", "slot", "position", "player_id", "team_id", "notes"],
        )
        w.writeheader()
        w.writerows(rows)
    unresolved = sum(1 for r in rows if not (r.get("player_id") or "").strip())
    print(f"Wrote {len(rows)} row(s) to {args.out} ({unresolved} without player_id).")


if __name__ == "__main__":
    main()
