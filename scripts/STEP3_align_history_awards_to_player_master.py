#!/usr/bin/env python3
"""Align ``history_awards*.csv`` ``player_id`` values with ``player_master.csv`` (FHM PlayerId).

For each award row:

- Keeps ``player_id`` when it matches a ``player_master`` ``PlayerId``.
- When the ID is missing, a spreadsheet null token, or not in ``player_master``, tries to resolve
  from ``notes`` (``unresolved_player=…`` or ``complex_winner=…``) via ``First Name`` + ``Last Name``.
- Writes the updated CSV (default: ``history_awards.sheet.csv``) and you can run
  ``LEAGUE_SLUG=bowl-historical python scripts/reimport_history_awards.py``.

Example::

  python scripts/align_history_awards_to_player_master.py \\
    --raw-dir data/imports/raw/bowl_historical \\
    --output data/imports/raw/bowl_historical/history_awards.sheet.csv

Run without ``--raw-dir`` in an interactive terminal to choose a league from a menu
(``bowl-historical``, ``bowl-fantasy``, ``bowl-cap``). Non-interactive runs default to Historical.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.config import LEAGUES

from scripts.convert_trophy_history_sheet import _strip_trophy_player_stat_suffix
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

_POS_PREFIX_RE = re.compile(r"^[A-Z]{1,3}-")


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().split())


def _norm_loose(s: str) -> str:
    """Lowercase name with punctuation relaxed (``St-Laurent`` vs ``St. Laurent``)."""
    t = re.sub(r"[.'\-]", " ", s or "")
    return _norm_name(t)


def _strip_pos_prefix(name: str) -> str:
    s = _strip_trophy_player_stat_suffix((name or "").strip())
    return _POS_PREFIX_RE.sub("", s).strip()


def _parse_notes_token(notes: str | None, prefix: str) -> str | None:
    if not notes:
        return None
    key = prefix.lower()
    for part in notes.split(";"):
        p = part.strip()
        if p.lower().startswith(key):
            return p.split("=", 1)[1].strip() or None
    return None


def _candidate_names_from_notes(notes: str | None) -> list[str]:
    """Ordered hints: unresolved player, then each ``/`` segment of ``complex_winner`` (Jennings tandems)."""
    out: list[str] = []
    u = _parse_notes_token(notes, "unresolved_player=")
    if u:
        out.append(u)
    c = _parse_notes_token(notes, "complex_winner=")
    if c:
        for seg in c.split("/"):
            s = _strip_pos_prefix(_strip_trophy_player_stat_suffix(seg.strip()))
            if s and s not in out:
                out.append(s)
    return out


def _is_null_token(pid: str) -> bool:
    return (pid or "").strip().lower() in ("", "null", "none", "nan")


def _load_master_index(path: Path) -> tuple[set[str], dict[str, list[str]]]:
    df = read_csv_normalized(path)
    valid: set[str] = set()
    by_norm: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = cell_val(r, "playerid", "player_id")
        if not pid:
            continue
        valid.add(str(pid).strip())
        fn = (cell_val(r, "first_name") or "").strip()
        ln = (cell_val(r, "last_name") or "").strip()
        full = f"{fn} {ln}".strip()
        if not full:
            continue
        pid_s = str(pid).strip()
        for key in {_norm_name(full), _norm_loose(full)}:
            lst = by_norm.setdefault(key, [])
            if pid_s not in lst:
                lst.append(pid_s)
    return valid, by_norm


def _pick_id(by_norm: dict[str, list[str]], display_name: str) -> tuple[str | None, str | None]:
    """Return (player_id or None, ambiguity_note or None)."""
    base = _strip_pos_prefix(_strip_trophy_player_stat_suffix(display_name))
    for key_fn in (_norm_name, _norm_loose):
        k = key_fn(base)
        ids = by_norm.get(k) or []
        if len(ids) == 1:
            return ids[0], None
        if len(ids) > 1:
            return None, f"ambiguous_master_name={base}"
    # Try last token as last name (e.g. multi-word)
    parts = base.split()
    if len(parts) >= 2:
        for key_fn in (_norm_name, _norm_loose):
            k2 = key_fn(f"{parts[0]} {parts[-1]}")
            ids2 = by_norm.get(k2) or []
            if len(ids2) == 1:
                return ids2[0], None
    return None, None


def _award_row_key(r: dict[str, str]) -> tuple[str, str]:
    season = (r.get("season") or "").strip()
    award = " ".join((r.get("award_name") or "").split())
    return season, award


def merge_manual_history_awards_into_sheet(raw_dir: Path) -> int:
    """Overlay ``history_awards.csv`` onto ``history_awards.sheet.csv`` (same season + award_name).

    The site importer prefers ``.sheet.csv``; merging keeps sheet-only rows while applying manual edits.
    """
    manual = raw_dir / "history_awards.csv"
    sheet = raw_dir / "history_awards.sheet.csv"
    if not manual.is_file() or not sheet.is_file():
        return 0
    with manual.open(newline="", encoding="utf-8-sig") as f:
        man = {_award_row_key(dict(x)): dict(x) for x in csv.DictReader(f)}
    with sheet.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0
    fieldnames = list(rows[0].keys())
    n = 0
    for r in rows:
        key = _award_row_key(r)
        if key not in man:
            continue
        m = man[key]
        for col in ("player_id", "team_id", "staff_id", "notes"):
            v = (m.get(col) or "").strip()
            if not v:
                continue
            if (r.get(col) or "").strip() != v:
                n += 1
            r[col] = v
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"Merged {n} cell change(s) from history_awards.csv into history_awards.sheet.csv")
    return n


def _merge_notes_drop_unresolved(notes: str | None, drop_unresolved: bool) -> str:
    if not notes or not drop_unresolved:
        return (notes or "").strip()
    parts_out: list[str] = []
    for part in notes.split(";"):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low.startswith("unresolved_player="):
            continue
        parts_out.append(p)
    return "; ".join(parts_out).strip()


def align_rows(
    rows: list[dict[str, str]],
    valid_ids: set[str],
    by_norm: dict[str, list[str]],
) -> tuple[list[dict[str, str]], int, int, int]:
    """Return (updated_rows, kept, remapped, unresolved)."""
    kept = remapped = unresolved = 0
    out: list[dict[str, str]] = []
    for row in rows:
        r = dict(row)
        notes = (r.get("notes") or "").strip()
        pid = (r.get("player_id") or "").strip()
        if _is_null_token(pid):
            pid = ""

        name_hints = _candidate_names_from_notes(notes) if notes else []
        new_pid = pid
        drop_unres = False
        orig_pid_for_invalid = pid

        def _try_hints() -> tuple[str | None, str | None]:
            for hint in name_hints:
                hit, amb = _pick_id(by_norm, hint)
                if hit:
                    return hit, amb
            return None, None

        if pid and pid in valid_ids:
            kept += 1
        elif pid and pid not in valid_ids:
            hit, amb = _try_hints()
            if hit:
                new_pid = hit
                remapped += 1
                drop_unres = bool(_parse_notes_token(notes, "unresolved_player="))
                if amb:
                    notes = (notes + "; " if notes else "") + amb
            else:
                tag = f"invalid_player_id={orig_pid_for_invalid}"
                if tag.lower() not in (notes or "").lower():
                    notes = f"{notes}; {tag}" if notes else tag
                new_pid = ""
                unresolved += 1
        elif not pid and name_hints:
            hit, amb = _try_hints()
            if hit:
                new_pid = hit
                remapped += 1
                drop_unres = bool(_parse_notes_token(notes, "unresolved_player="))
                if amb:
                    notes = (notes + "; " if notes else "") + amb
            else:
                unresolved += 1
        else:
            kept += 1  # no player row (team / coach / empty)

        if drop_unres:
            notes = _merge_notes_drop_unresolved(notes, True)
        final_pid = new_pid
        if "no_winner=1" in (notes or "").lower() and final_pid:
            final_pid = ""
        r["player_id"] = final_pid
        r["notes"] = notes
        out.append(r)
    return out, kept, remapped, unresolved


def _raw_dir_for_league_entry(raw_import_dir: str) -> Path:
    return (_REPO / "data" / "imports" / "raw" / raw_import_dir).resolve()


def _prompt_raw_dir() -> Path:
    """Ask which league raw folder to use (stdin/stdout must be TTY)."""
    rows: list[tuple[int, str, str, Path]] = []
    print("Align history awards — pick league (raw import folder):\n")
    for i, e in enumerate(LEAGUES, start=1):
        p = _raw_dir_for_league_entry(e.raw_import_dir)
        pm = "has player_master" if (p / "player_master.csv").is_file() else "no player_master.csv"
        try:
            rel = p.relative_to(_REPO)
        except ValueError:
            rel = p
        print(f"  {i}) {e.display_name}  [{e.slug}]")
        print(f"      {rel}  ({pm})\n")
        rows.append((i, e.slug, e.display_name, p))
    default_i = 1
    while True:
        raw_in = input(f"Enter 1–{len(rows)} [default {default_i}]: ").strip()
        if not raw_in:
            return rows[default_i - 1][3]
        if not raw_in.isdigit():
            print("Please enter a number.", file=sys.stderr)
            continue
        n = int(raw_in)
        if 1 <= n <= len(rows):
            return rows[n - 1][3]
        print(f"Choose between 1 and {len(rows)}.", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Example::", 1)[0].strip())
    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="League raw import folder (contains player_master.csv and history awards CSV). "
        "Omit for an interactive menu in a terminal; otherwise defaults to Historical.",
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Input history awards CSV (default: history_awards.sheet.csv or history_awards.csv)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV (default: same as --input if set, else history_awards.sheet.csv)",
    )
    ap.add_argument(
        "--no-merge-manual",
        action="store_true",
        help="Do not copy cells from history_awards.csv into history_awards.sheet.csv first",
    )
    args = ap.parse_args()
    if args.raw_dir is not None:
        raw = args.raw_dir.resolve()
    elif sys.stdin.isatty() and sys.stdout.isatty():
        raw = _prompt_raw_dir()
    else:
        raw = _raw_dir_for_league_entry("bowl_historical")
    mp = raw / "player_master.csv"
    if not mp.is_file():
        print(f"player_master.csv not found: {mp}", file=sys.stderr)
        return 1
    if not args.no_merge_manual:
        merge_manual_history_awards_into_sheet(raw)
    inp = args.input
    if inp is None:
        sheet_p = raw / "history_awards.sheet.csv"
        if sheet_p.is_file():
            inp = sheet_p
        else:
            for name in ("history_awards.csv", "awards_history.csv"):
                p = raw / name
                if p.is_file():
                    inp = p
                    break
    if inp is None:
        print("No history_awards*.csv in raw dir", file=sys.stderr)
        return 1
    inp = inp.resolve()
    outp = args.output.resolve() if args.output else inp

    valid, by_norm = _load_master_index(mp)
    with inp.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else [
        "season",
        "award_name",
        "player_id",
        "team_id",
        "staff_id",
        "notes",
    ]
    out_rows, kept, remapped, unresolved = align_rows(rows, valid, by_norm)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(
        f"Wrote {len(out_rows)} row(s) to {outp} "
        f"(ids_ok_or_blank={kept}, remapped={remapped}, still_unresolved_hints={unresolved}) "
        f"using {len(valid)} player_master ids."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
