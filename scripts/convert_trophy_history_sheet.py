#!/usr/bin/env python3
"""Convert wide-format ``Trophy History`` spreadsheets into ``history_awards.csv``.

Your sheet layout (e.g. ``BOWL-Fantasy League Stats_Financials_Data - Trophy History.csv``) is:

- A **title** row: one cell per award column group (trophy names left-to-right).
- A **subtitle** row (ignored for import).
- A **header** row: repeating ``Season, Team, POS-Player`` or ``Season, Team, Team Name`` per group,
  separated by empty columns (CSV ``,,`` gaps). Stride is inferred from ``Season`` cell positions.
- **Data** rows: same stride; first cell of each group is usually ``YYYY-YY`` season label.

Output for the site importer (``scripts/import_pipeline/runner.py`` → ``import_history_awards``):

- ``season`` — must match ``Season.label`` or ``Season.fhm_season_id`` in the league DB.
- ``award_name`` — text shown in the Awards panel.
- ``player_id`` — ``Player.fhm_player_id`` (optional if unresolved).
- ``team_id`` — ``Team.fhm_team_id`` or ``Team.abbreviation`` (optional).
- ``notes`` — free text; importer stores it (panel does not render it today).

**Optional DB resolution** (Fantasy/Cap/Historical): pass ``--league-slug`` and the script will try to
fill ``player_id`` / ``team_id`` from ``player_name_raw`` / ``team_name_raw`` using exact or
case-insensitive ``Player.full_name`` and ``Team.name`` / ``Team.nickname`` / ``Team.abbreviation``.

**Trophy graphics (when you have art):**

1. Add PNG (or WebP) files under a league folder, for example::

     app/static/img/history/trophies/bowl_fantasy/art-ross.png

2. Either keep filenames aligned with a future template change, or for now store a hint in
   ``notes`` such as ``trophy_img=img/history/trophies/bowl_fantasy/art-ross.png`` so you can wire
   ``history.html`` later without another CSV column. The DB model has no dedicated image field yet.

Examples::

  python scripts/convert_trophy_history_sheet.py \\
    --input \"C:/Users/keeno/Downloads/BOWL-Fantasy League Stats_Financials_Data - Trophy History.csv\" \\
    --output data/imports/raw/bowl_fantasy/history_awards.csv

  python scripts/convert_trophy_history_sheet.py --input sheet.csv --output out.csv --league-slug bowl-fantasy

  python scripts/convert_trophy_history_sheet.py --input sheet.csv --stdout --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

_SEASON_START = re.compile(r"^\d{4}-\d{2}")


def _read_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def _nonempty_cells(row: list[str]) -> list[str]:
    return [c.strip() for c in row if c and c.strip()]


def _is_blank_row(row: list[str]) -> bool:
    return not any((c or "").strip() for c in row)


def _parse_header_stride(header: list[str]) -> tuple[list[int], int]:
    """Return start index of each ``Season`` column and stride to next block."""
    starts: list[int] = []
    for i, c in enumerate(header):
        if (c or "").strip() == "Season":
            starts.append(i)
    if len(starts) < 1:
        return [], 0
    stride = starts[1] - starts[0] if len(starts) > 1 else len(header)
    return starts, stride


def _trophy_names_from_title(title_row: list[str], n_blocks: int) -> list[str]:
    """Left-to-right non-empty title cells; must align with block count."""
    names = [c.strip() for c in title_row if c and c.strip()]
    if len(names) == n_blocks:
        return names
    if len(names) > n_blocks:
        return names[:n_blocks]
    # Pad short title rows with generic labels
    out = list(names)
    while len(out) < n_blocks:
        out.append(f"Award {len(out) + 1}")
    return out


def _third_header_label(header: list[str], start: int, stride: int) -> str:
    idx = start + 2
    if idx < len(header):
        return (header[idx] or "").strip()
    return ""


def _cell(row: list[str], idx: int) -> str:
    if idx < len(row):
        return (row[idx] or "").strip()
    return ""


def _looks_like_data_row(row: list[str], season_starts: list[int]) -> bool:
    if not season_starts:
        return False
    first_season = _cell(row, season_starts[0])
    return bool(_SEASON_START.match(first_season))


def _looks_like_section_title_row(row: list[str]) -> bool:
    """Heuristic: first cell is not Season/Team and row has several tokens (next rows are subtitle+header)."""
    c0 = (row[0] or "").strip()
    if c0 in ("Season", "Team"):
        return False
    if _SEASON_START.match(c0):
        return False
    nonempty = _nonempty_cells(row)
    return len(nonempty) >= 2


def parse_trophy_sheet(rows: list[list[str]]) -> list[dict[str, str]]:
    """Parse wide trophy blocks into flat award dicts (raw names, no DB ids)."""
    out: list[dict[str, str]] = []
    i = 0
    while i < len(rows):
        if _is_blank_row(rows[i]):
            i += 1
            continue
        # Need title + subtitle + header = Season row
        if i + 2 >= len(rows):
            i += 1
            continue
        title_row = rows[i]
        _subtitle = rows[i + 1]
        header = rows[i + 2]
        h0 = (header[0] or "").strip()
        h1 = (header[1] or "").strip() if len(header) > 1 else ""
        if h0 != "Season" or h1 != "Team":
            i += 1
            continue
        season_starts, stride = _parse_header_stride(header)
        if not season_starts or stride < 3:
            i += 1
            continue
        n_blocks = len(season_starts)
        trophy_names = _trophy_names_from_title(title_row, n_blocks)
        third_labels = [_third_header_label(header, s, stride) for s in season_starts]
        i += 3
        while i < len(rows):
            drow = rows[i]
            if _is_blank_row(drow):
                i += 1
                break
            if _looks_like_section_title_row(drow):
                break
            if not _looks_like_data_row(drow, season_starts):
                # Skip stray rows (e.g. "(tie)" continuation) unless they have season pattern
                if any(_SEASON_START.match(_cell(drow, s)) for s in season_starts):
                    pass
                else:
                    i += 1
                    continue
            for b in range(n_blocks):
                start = season_starts[b]
                season = _cell(drow, start)
                team_field = _cell(drow, start + 1)
                third = _cell(drow, start + 2)
                if not season and not third and not team_field:
                    continue
                award = trophy_names[b] if b < len(trophy_names) else f"Award {b + 1}"
                kind = "team" if third_labels[b].lower() == "team name" else "player"
                rec: dict[str, str] = {
                    "season": season,
                    "award_name": award,
                    "player_name_raw": "",
                    "team_name_raw": "",
                    "notes": "",
                }
                if kind == "team":
                    # Third column is winning franchise name in your sheet
                    rec["team_name_raw"] = third or team_field
                    if third and team_field and third != team_field:
                        rec["notes"] = f"sheet_team_col={team_field}"
                else:
                    rec["player_name_raw"] = third
                    rec["team_name_raw"] = team_field
                    if "/" in third or "(" in third:
                        rec["notes"] = "complex_winner=" + third.replace("\n", " ").strip()
                out.append(rec)
            i += 1
        # do not increment i here; outer loop continues from current i
    return out


def _resolve_with_db(
    records: list[dict[str, str]],
    league_slug: str,
) -> list[dict[str, str]]:
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from app import create_app
    from app.config import make_league_config
    from app.models import Player, Team, db
    from sqlalchemy import func, select

    app = create_app(make_league_config(league_slug))
    with app.app_context():

        def find_player(name: str) -> str | None:
            if not name:
                return None
            n = name.strip()
            row = db.session.scalars(
                select(Player).where(func.lower(Player.full_name) == func.lower(n)).limit(1)
            ).first()
            if row and row.fhm_player_id:
                return str(row.fhm_player_id)
            row = db.session.scalars(select(Player).where(Player.full_name == n).limit(1)).first()
            if row and row.fhm_player_id:
                return str(row.fhm_player_id)
            return None

        def _norm(s: str) -> str:
            return " ".join((s or "").lower().split())

        def find_team(name: str) -> str | None:
            if not name:
                return None
            n = _norm(name)
            teams = db.session.scalars(select(Team)).all()
            for t in teams:
                variants = {
                    _norm(t.name or ""),
                    _norm(t.nickname or ""),
                    _norm(t.abbreviation or ""),
                    _norm(f"{t.city or ''} {t.nickname or ''}".strip()),
                    _norm(f"{t.name or ''} {t.nickname or ''}".strip()),
                    _norm(f"{t.city or ''} {t.name or ''}".strip()),
                }
                if n and n in variants:
                    return t.fhm_team_id or t.abbreviation
            for t in teams:
                nm = _norm(t.name or "")
                if n and (n in nm or nm in n):
                    return t.fhm_team_id or t.abbreviation
            return None

        resolved: list[dict[str, str]] = []
        for r in records:
            pid = find_player(r.get("player_name_raw") or "")
            tid = find_team(r.get("team_name_raw") or "")
            notes = (r.get("notes") or "").strip()
            if not pid and (r.get("player_name_raw") or "").strip():
                if notes:
                    notes += "; "
                notes += "unresolved_player=" + (r.get("player_name_raw") or "").strip()
            if not tid and (r.get("team_name_raw") or "").strip():
                if notes:
                    notes += "; "
                notes += "unresolved_team=" + (r.get("team_name_raw") or "").strip()
            resolved.append(
                {
                    "season": r.get("season") or "",
                    "award_name": r.get("award_name") or "",
                    "player_id": pid or "",
                    "team_id": tid or "",
                    "notes": notes,
                }
            )
        return resolved


def _write_import_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["season", "award_name", "player_id", "team_id", "notes"],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "season": r.get("season", ""),
                    "award_name": r.get("award_name", ""),
                    "player_id": r.get("player_id", ""),
                    "team_id": r.get("team_id", ""),
                    "notes": r.get("notes", ""),
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Examples::", 1)[0].strip())
    ap.add_argument("--input", type=Path, required=True, help="Source wide-format Trophy History CSV")
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write importable history_awards.csv here (default: stdout only if --stdout)",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="Print importable CSV to stdout instead of --output",
    )
    ap.add_argument(
        "--league-slug",
        default=None,
        help="If set (e.g. bowl-fantasy), resolve player/team names against that league SQLite DB",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write files",
    )
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    rows_in = _read_rows(args.input)
    flat = parse_trophy_sheet(rows_in)
    if args.dry_run:
        print(f"Parsed {len(flat)} award rows from {args.input}")
        return 0

    if args.league_slug:
        out_rows = _resolve_with_db(flat, args.league_slug.strip())
    else:
        out_rows = []
        for r in flat:
            notes = (r.get("notes") or "").strip()
            pr = (r.get("player_name_raw") or "").strip()
            tr = (r.get("team_name_raw") or "").strip()
            if pr:
                notes = (notes + "; " if notes else "") + "player_name=" + pr
            if tr:
                notes = (notes + "; " if notes else "") + "team_name=" + tr
            out_rows.append(
                {
                    "season": r.get("season", ""),
                    "award_name": r.get("award_name", ""),
                    "player_id": "",
                    "team_id": "",
                    "notes": notes,
                }
            )

    if args.stdout:
        buf = io.StringIO()
        w = csv.DictWriter(
            buf,
            fieldnames=["season", "award_name", "player_id", "team_id", "notes"],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
        sys.stdout.write(buf.getvalue())
        return 0

    if not args.output:
        print("Specify --output or --stdout", file=sys.stderr)
        return 1

    _write_import_csv(args.output, out_rows)
    print(f"Wrote {len(out_rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
