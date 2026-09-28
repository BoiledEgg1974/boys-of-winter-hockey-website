"""Inspect record-broken Discord queue, deploy sidecars, and where breaks originate.

Run locally or on PythonAnywhere (from repo root):

  python scripts/inspect_record_broken_discord.py
  python scripts/inspect_record_broken_discord.py --league bowl-historical --limit 30

Where "record broken" comes from (code paths):

1. **Game (single-game) records** — ``app/services/game_records.py``
   ``sync_game_record_baselines()`` compares each metric/segment/scope leader from
   the game log to ``game_record_baselines`` rows. Strict improvements become
   ``GameRecordBreak`` (stashed via ``stash_game_record_break``). After FHM import,
   ``rebuild.refresh_after_import`` drains the stash into
   ``notify_record_breaks_after_import(..., game_breaks=...)``.

2. **Season / franchise season / all-time / team board records** —
   ``app/services/record_broken_discord.py`` ``collect_current_record_holders()``
   reads live #1 rows from season boards, all-time queries, and team record pages.
   ``detect_snapshot_breaks()`` diffs against ``record_leader_snapshots`` (previous
   import). Each strict improvement enqueues Discord with ``source_id`` like
   ``{snapshot_key}:{entity_key}:{value}`` (e.g. ``season:league:rs:goals:player:9:61``).

3. **Deploy-db (production)** — Local import writes sidecar
   ``instance/.deploy_discord_records/<slug>.json``. After DB promote,
   ``scripts/notify_discord_after_db_deploy.py`` enqueues those events, or if the
   sidecar is missing, rebuilds from ``instance/.deploy_discord_records_live/<slug>.json``
   via ``events_from_live_record_state_diff()``.

4. **Admin test** — ``enqueue_test_event`` on Discord Integration (now sends sample
   old/new lines for ``record_broken``).

Incomplete payloads (no ``new_record_line`` / title) produce blank Discord posts;
``record_broken_discord_payload_is_complete()`` blocks new enqueues.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_sidecar(slug: str) -> None:
    from app.services.deploy_discord_records import (
        load_deploy_record_break_events,
        load_live_record_state,
    )

    events = load_deploy_record_break_events(slug)
    print(f"\n=== Sidecar .deploy_discord_records/{slug}.json ({len(events)} events) ===")
    for ev in events[:50]:
        sid = ev.get("source_id")
        p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        from app.services.record_broken_discord import (
            describe_record_broken_source,
            record_broken_discord_payload_is_complete,
        )

        ok = record_broken_discord_payload_is_complete(p)
        print(f"  {'OK' if ok else 'INCOMPLETE'} {describe_record_broken_source(payload=p, source_id=str(sid or ''))}")
        if not ok:
            print(f"    keys={sorted(p.keys())}")

    live = load_live_record_state(slug)
    if live:
        snaps = live.get("snapshots") or {}
        games = live.get("game_baselines") or {}
        print(
            f"\n=== Live stash .deploy_discord_records_live/{slug}.json "
            f"snapshots={len(snaps) if isinstance(snaps, dict) else 0} "
            f"game_baselines={len(games) if isinstance(games, dict) else 0} ==="
        )


def _print_site_queue(league: str | None, limit: int) -> None:
    import sqlite3

    from app.config import BASE_DIR

    db_path = Path(BASE_DIR) / "instance" / "site_membership.db"
    if not db_path.is_file():
        print(f"No site DB at {db_path}")
        return
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if league:
        cur.execute(
            """
            SELECT id, league_slug, status, created_at, sent_at, payload_json, idempotency_key, last_error
            FROM discord_outbound_events
            WHERE event_key = 'record_broken' AND league_slug = ?
            ORDER BY id DESC LIMIT ?
            """,
            (league, limit),
        )
    else:
        cur.execute(
            """
            SELECT id, league_slug, status, created_at, sent_at, payload_json, idempotency_key, last_error
            FROM discord_outbound_events
            WHERE event_key = 'record_broken'
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
    rows = cur.fetchall()
    print(f"\n=== Site queue discord_outbound_events (record_broken, n={len(rows)}) ===")
    from app.services.record_broken_discord import (
        describe_record_broken_source,
        record_broken_discord_payload_is_complete,
    )

    for r in rows:
        try:
            p = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            p = {}
        ok = record_broken_discord_payload_is_complete(p) if isinstance(p, dict) else False
        src = describe_record_broken_source(payload=p if isinstance(p, dict) else {})
        print(
            f"  id={r['id']} {r['league_slug']} status={r['status']} "
            f"created={r['created_at']} sent={r['sent_at'] or '-'} "
            f"{'OK' if ok else 'INCOMPLETE'}"
        )
        print(f"    {src}")
        if r["last_error"]:
            print(f"    last_error={r['last_error'][:200]}")
        if not ok and isinstance(p, dict):
            print(f"    payload_keys={sorted(p.keys())}")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", help="Filter site queue / sidecar to one hockey slug.")
    ap.add_argument("--limit", type=int, default=25, help="Max outbound rows to print.")
    ap.add_argument("--sidecar-only", action="store_true")
    ap.add_argument("--queue-only", action="store_true")
    args = ap.parse_args()
    slug = str(args.league or "").strip()
    if not args.queue_only:
        if slug:
            _print_sidecar(slug)
        else:
            from app.services.deploy_discord_records import list_deploy_discord_records_files

            files = list_deploy_discord_records_files()
            if not files:
                print("No instance/.deploy_discord_records/*.json sidecars (often gitignored; check after local import).")
            for path in files:
                _print_sidecar(path.stem)
    if not args.sidecar_only:
        _print_site_queue(slug or None, max(1, int(args.limit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
