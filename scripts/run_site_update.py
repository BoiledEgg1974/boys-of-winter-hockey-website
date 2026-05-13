#!/usr/bin/env python3
"""Single entry point for league CSV refresh and PythonAnywhere deploy.

Child scripts always run in a safe order so you do not have to remember STEP1 → STEP3 → STEP2.

Usage (from repo root)::

    python scripts/run_site_update.py
    python scripts/run_site_update.py to-live --yes-push
    python scripts/run_site_update.py to-live --yes-push -- --remote-pip
    python scripts/run_site_update.py local --allow-stale
    python scripts/run_site_update.py deploy --dry-run
    python scripts/run_site_update.py bowl --no-deploy

The first token may be a *workflow* name; if you omit it, ``to-live`` is assumed.

Workflows
~~~~~~~~~

``to-live`` (default)
    1. ``STEP1_update_from_saved_game.py --no-pa-deploy`` + your STEP1 flags (copy exports,
       STEP3 align per league, local imports + sheet reimport, optional git push).
    2. ``STEP2_pythonanywhere.py deploy --repo-csv`` + optional STEP2 flags after ``--``.

    Example with remote pip during deploy::

        python scripts/run_site_update.py to-live --yes-push -- --remote-pip

``local``
    STEP1 only, always with ``--no-pa-deploy`` (no PythonAnywhere in this run).

``deploy``
    STEP2 deploy only: ``STEP2_pythonanywhere.py deploy --repo-csv`` + any flags you pass
    (e.g. ``--dry-run``, ``--remote-pip``). Use when CSVs are already in the repo.

``bowl``
    Runs ``BOWL-Site-Update.py`` (extra Historical awards pass + deploy). All following
    arguments are passed through unchanged.

-h, --help
    Show this text.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WORKFLOWS = frozenset({"to-live", "local", "deploy", "bowl"})

REPO_ROOT = Path(__file__).resolve().parents[1]
STEP1 = REPO_ROOT / "scripts" / "STEP1_update_from_saved_game.py"
STEP2 = REPO_ROOT / "scripts" / "STEP2_pythonanywhere.py"
BOWL = REPO_ROOT / "scripts" / "BOWL-Site-Update.py"


def _run(cmd: list[str]) -> int:
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd, cwd=REPO_ROOT)


def _split_step1_step2(tail: list[str]) -> tuple[list[str], list[str]]:
    if "--" in tail:
        i = tail.index("--")
        return tail[:i], tail[i + 1 :]
    return tail, []


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0

    if argv[0] in WORKFLOWS:
        workflow = argv[0]
        tail = argv[1:]
    else:
        workflow = "to-live"
        tail = argv[:]

    py = sys.executable

    if workflow == "bowl":
        if not BOWL.is_file():
            print(f"Missing {BOWL}", file=sys.stderr)
            return 1
        return _run([py, str(BOWL), *tail])

    if workflow == "deploy":
        if not STEP2.is_file():
            print(f"Missing {STEP2}", file=sys.stderr)
            return 1
        return _run([py, str(STEP2), "deploy", "--repo-csv", *tail])

    if workflow in ("to-live", "local"):
        if not STEP1.is_file():
            print(f"Missing {STEP1}", file=sys.stderr)
            return 1
        if "--pa-deploy" in tail:
            print(
                "run_site_update: do not pass --pa-deploy for to-live/local — PythonAnywhere "
                "runs after STEP1 in to-live, and is omitted for local.",
                file=sys.stderr,
            )
            return 1

        step1_tail, step2_tail = _split_step1_step2(tail)
        if workflow == "local" and step2_tail:
            print(
                "run_site_update: arguments after -- are only used with workflow ``to-live``.",
                file=sys.stderr,
            )
            return 1

        code = _run([py, str(STEP1), "--no-pa-deploy", *step1_tail])
        if code != 0:
            return code
        if workflow != "to-live":
            return 0
        if not STEP2.is_file():
            print(f"Missing {STEP2}", file=sys.stderr)
            return 1
        return _run([py, str(STEP2), "deploy", "--repo-csv", *step2_tail])

    print(f"Unknown workflow {workflow!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
