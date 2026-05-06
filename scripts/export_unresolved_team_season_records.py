#!/usr/bin/env python3
"""Export unresolved Team ID rows from team_season_records_template.csv.

Writes `<raw_dir>/team_season_records_unresolved_team_ids.csv` with unresolved rows and
lightweight suggestions from team_data.csv / teams.csv.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data" / "imports" / "raw"
TARGET_NAME = "team_season_records_template.csv"
OUT_NAME = "team_season_records_unresolved_team_ids.csv"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace(".", " ").replace("-", " ").split())


def _load_known_teams(raw_dir: Path) -> tuple[set[str], list[tuple[str, str, str]]]:
    valid_ids: set[str] = set()
    known: list[tuple[str, str, str]] = []  # (display_name, team_id, abbr)

    team_data = raw_dir / "team_data.csv"
    if team_data.is_file():
        df = read_csv_normalized(team_data)
        for _, row in df.iterrows():
            r = row.to_dict()
            tid = (cell_val(r, "teamid", "team_id", "id") or "").strip()
            if not tid:
                continue
            city = (cell_val(r, "name") or "").strip()
            nick = (cell_val(r, "nickname", "nick") or "").strip()
            abbr = (cell_val(r, "abbr", "abbreviation") or "").strip()
            name = f"{city} {nick}".strip() or city or nick
            valid_ids.add(tid)
            known.append((name, tid, abbr))

    teams_csv = raw_dir / "teams.csv"
    if teams_csv.is_file():
        df = read_csv_normalized(teams_csv)
        for _, row in df.iterrows():
            r = row.to_dict()
            tid = (cell_val(r, "fhm_team_id", "team_id", "id") or "").strip()
            if not tid:
                continue
            city = (cell_val(r, "city") or "").strip()
            nick = (cell_val(r, "nickname", "nick") or "").strip()
            name = (cell_val(r, "name", "team_name") or "").strip()
            abbr = (cell_val(r, "abbreviation", "abbr", "team_abbr") or "").strip()
            display = f"{city} {nick}".strip() or name
            valid_ids.add(tid)
            known.append((display, tid, abbr))

    # Deduplicate by (normalized name, id)
    dedup: dict[tuple[str, str], tuple[str, str, str]] = {}
    for name, tid, abbr in known:
        dedup[(_norm(name), tid)] = (name, tid, abbr)
    return valid_ids, list(dedup.values())


def _suggest_name(needle: str, known_names: list[str]) -> str:
    if not needle:
        return ""
    matches = difflib.get_close_matches(_norm(needle), [_norm(n) for n in known_names], n=1, cutoff=0.6)
    if not matches:
        return ""
    hit_norm = matches[0]
    for raw in known_names:
        if _norm(raw) == hit_norm:
            return raw
    return ""


def export_unresolved(raw_dir: Path) -> tuple[int, Path]:
    src = raw_dir / TARGET_NAME
    if not src.is_file():
        return 0, raw_dir / OUT_NAME

    valid_ids, known = _load_known_teams(raw_dir)
    known_names = [k[0] for k in known]
    known_by_name = {k[0]: k for k in known}

    rows = list(csv.DictReader(src.open(newline="", encoding="utf-8-sig")))
    out_rows: list[dict[str, str]] = []
    for r in rows:
        team_id = (r.get("Team ID") or "").strip()
        if team_id and team_id in valid_ids:
            continue
        team_name = (r.get("Team Name Override") or "").strip()
        suggestion_name = _suggest_name(team_name, known_names)
        suggestion_id = ""
        suggestion_abbr = ""
        if suggestion_name:
            _, suggestion_id, suggestion_abbr = known_by_name[suggestion_name]
        out_rows.append(
            {
                "Year": (r.get("Year") or "").strip(),
                "Team ID": team_id,
                "Team Name Override": team_name,
                "Conference ID": (r.get("Conference ID") or "").strip(),
                "Division ID": (r.get("Division ID") or "").strip(),
                "Suggested Team Name": suggestion_name,
                "Suggested Team ID": suggestion_id,
                "Suggested Team Abbr": suggestion_abbr,
            }
        )

    out_path = raw_dir / OUT_NAME
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "Year",
            "Team ID",
            "Team Name Override",
            "Conference ID",
            "Division ID",
            "Suggested Team Name",
            "Suggested Team ID",
            "Suggested Team Abbr",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)
    return len(out_rows), out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--raw-dir",
        action="append",
        default=[],
        help="Raw import directory path (repeatable). Defaults to bowl_cap + bowl_historical.",
    )
    args = ap.parse_args()

    raw_dirs = [Path(p).resolve() for p in args.raw_dir] if args.raw_dir else [
        (RAW_ROOT / "bowl_cap").resolve(),
        (RAW_ROOT / "bowl_historical").resolve(),
    ]

    for raw in raw_dirs:
        count, out_path = export_unresolved(raw)
        print(f"{raw.name}: wrote {count} unresolved rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
