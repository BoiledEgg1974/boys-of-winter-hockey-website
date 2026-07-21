"""Reconstruct real-world first-final timestamps from schedules.csv git history.

Walks every commit that touched ``data/imports/raw/<league>/schedules.csv`` and
records, per FHM game id, the commit time of the first snapshot where the game
appears as played. Commit times track the local import/deploy pipeline runs, so
they are a faithful proxy for when each game's final result went live on the
site (the moment BOWL Six should have recorded a marker).

Run locally from the repo root:

  python scripts/bowl_six_reconstruct_final_markers.py --since 2026-05-25

Output: ``data/imports/bowl_six_final_marker_reconstruction.json`` mapping
league slug -> {fhm_game_id: first_final_utc_iso}.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEAGUES = {
    "bowl-cap": "data/imports/raw/bowl_cap/schedules.csv",
    "bowl-fantasy": "data/imports/raw/bowl_fantasy/schedules.csv",
    "bowl-historical": "data/imports/raw/bowl_historical/schedules.csv",
}

DEFAULT_OUTPUT = "data/imports/bowl_six_final_marker_reconstruction.json"


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _commits(path: str, since: str) -> list[tuple[str, datetime]]:
    """(sha, commit time as naive UTC) oldest first."""
    out = _git(
        "log",
        "--reverse",
        f"--since={since}",
        "--format=%H %cI",
        "--",
        path,
    )
    rows: list[tuple[str, datetime]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, iso = line.split(" ", 1)
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc).replace(tzinfo=None)
        rows.append((sha, dt))
    return rows


def _played_game_ids(sha: str, path: str) -> set[str]:
    raw = _git("show", f"{sha}:{path}")
    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    played: set[str] = set()
    for row in reader:
        if str(row.get("Played", "")).strip() != "1":
            continue
        gid = str(row.get("Game Id", "")).strip()
        if gid:
            played.add(gid)
    return played


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--since",
        default="2026-05-25",
        help="Only walk commits after this date (avoid older-season game id reuse).",
    )
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    result: dict[str, dict[str, str]] = {}
    for slug, path in LEAGUES.items():
        commits = _commits(path, args.since)
        first_final: dict[str, str] = {}
        for sha, dt in commits:
            for gid in _played_game_ids(sha, path):
                first_final.setdefault(gid, dt.isoformat())
        result[slug] = first_final
        print(
            f"{slug}: {len(commits)} commit(s) since {args.since}, "
            f"{len(first_final)} played game id(s)",
            file=sys.stderr,
        )

    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
