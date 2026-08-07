"""Import Godot EXPORT CSV files into racing league tables."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.racing_models import (
    RacingApSuggestion,
    RacingCircuit,
    RacingCircuitStanding,
    RacingEvent,
    RacingEventResult,
    RacingImportBatch,
)
from app.services.racing_csv import (
    cell_bool,
    cell_float,
    cell_int,
    classify_export_filename,
    formula_circuit_points_for_position,
    list_export_csvs,
    normalize_name_key,
    parse_export_stamp,
    read_csv_dicts,
)
from app.services.racing_racers import resolve_racer_by_name
from app.services.racing_rewards import (
    SCHEDULE_CIRCUIT_AP,
    SCHEDULE_CIRCUIT_CP,
    SCHEDULE_RACE_AP,
    SCHEDULE_RACE_CP,
    amount_for_place,
    ensure_default_reward_tiers,
)


def _active_or_new_circuit(session: Session, *, stamp: str | None, name: str | None = None) -> RacingCircuit:
    circuit = session.scalar(
        select(RacingCircuit)
        .where(RacingCircuit.status == "active")
        .order_by(RacingCircuit.id.desc())
        .limit(1)
    )
    if circuit is None:
        circuit = RacingCircuit(
            name=name or "Circuit 1",
            external_key=stamp,
            status="active",
            started_at=datetime.utcnow(),
        )
        session.add(circuit)
        session.flush()
    return circuit


def _get_or_create_event(
    session: Session,
    circuit: RacingCircuit,
    *,
    event_number: int,
    event_kind: str,
    track_name: str | None,
    stamp: str | None,
) -> RacingEvent:
    event = session.scalar(
        select(RacingEvent).where(
            RacingEvent.circuit_id == int(circuit.id),
            RacingEvent.event_number == int(event_number),
        ).limit(1)
    )
    if event is None:
        event = RacingEvent(
            circuit_id=int(circuit.id),
            event_number=int(event_number),
            event_kind=event_kind,
            track_name=track_name,
            title=track_name or f"Event {event_number}",
            export_stamp=stamp,
            occurred_at=datetime.utcnow(),
        )
        session.add(event)
        session.flush()
    else:
        if track_name:
            event.track_name = track_name
            event.title = track_name
        if stamp:
            event.export_stamp = stamp
    return event


def _mark_batch(
    session: Session,
    *,
    filename: str,
    kind: str,
    stamp: str | None,
    row_count: int,
    circuit_id: int | None = None,
    event_id: int | None = None,
    notes: str | None = None,
) -> None:
    existing = session.scalar(
        select(RacingImportBatch).where(RacingImportBatch.filename == filename).limit(1)
    )
    if existing is None:
        session.add(
            RacingImportBatch(
                filename=filename,
                kind=kind,
                export_stamp=stamp,
                row_count=row_count,
                circuit_id=circuit_id,
                event_id=event_id,
                notes=notes,
            )
        )
    else:
        existing.row_count = row_count
        existing.export_stamp = stamp
        existing.circuit_id = circuit_id
        existing.event_id = event_id
        existing.notes = notes
        existing.imported_at = datetime.utcnow()


def _upsert_ap_suggestion(
    session: Session,
    *,
    scope: str,
    driver_name: str,
    amount: int,
    rank: int | None,
    event_id: int | None,
    circuit_id: int | None,
    source_ref: str,
    currency: str = "ap",
) -> None:
    if amount <= 0:
        return
    key = normalize_name_key(driver_name)
    racer = resolve_racer_by_name(session, driver_name)
    existing = session.scalar(
        select(RacingApSuggestion).where(RacingApSuggestion.source_ref == source_ref).limit(1)
    )
    if existing is not None:
        if existing.status == "granted":
            return
        existing.amount = amount
        existing.rank = rank
        existing.currency = currency
        existing.racer_id = int(racer.id) if racer else None
        existing.driver_name = driver_name
        existing.driver_key = key
        return
    session.add(
        RacingApSuggestion(
            scope=scope,
            currency=currency,
            event_id=event_id,
            circuit_id=circuit_id,
            racer_id=int(racer.id) if racer else None,
            driver_key=key,
            driver_name=driver_name,
            amount=amount,
            rank=rank,
            status="pending",
            source_ref=source_ref,
        )
    )


def _import_formula_race_results(
    session: Session, path: Path, rows: list[dict[str, str]], stamp: str | None
) -> dict[str, Any]:
    circuit = _active_or_new_circuit(session, stamp=stamp, name="Formula Circuit")
    # Group by race number
    by_race: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        n = cell_int(row, "race", default=1)
        by_race.setdefault(n, []).append(row)
    events_touched = 0
    results_written = 0
    for race_num, race_rows in sorted(by_race.items()):
        track = next((r.get("track") for r in race_rows if r.get("track")), None)
        event = _get_or_create_event(
            session,
            circuit,
            event_number=race_num,
            event_kind="race",
            track_name=track,
            stamp=stamp,
        )
        # Replace results for this event
        for old in list(session.scalars(select(RacingEventResult).where(RacingEventResult.event_id == int(event.id)))):
            session.delete(old)
        session.flush()
        for row in race_rows:
            pos = cell_int(row, "position", default=0)
            if pos <= 0:
                continue
            driver = row.get("driver") or row.get("name") or f"Driver {pos}"
            racer = resolve_racer_by_name(session, driver)
            pts = formula_circuit_points_for_position(pos)
            session.add(
                RacingEventResult(
                    event_id=int(event.id),
                    racer_id=int(racer.id) if racer else None,
                    position=pos,
                    car_number=cell_int(row, "number", default=0) or None,
                    driver_name=driver,
                    controller=row.get("controller") or None,
                    finished=cell_bool(row, "finished"),
                    eliminated=cell_bool(row, "eliminated"),
                    circuit_points=pts,
                    channel_points=amount_for_place(session, SCHEDULE_RACE_CP, pos),
                    gear=cell_int(row, "gear", default=0) or None,
                    wear=cell_int(row, "wear", default=0) or None,
                    lap=cell_int(row, "lap", default=0) or None,
                    summary=row.get("summary") or None,
                )
            )
            _upsert_ap_suggestion(
                session,
                scope="race",
                currency="ap",
                driver_name=driver,
                amount=amount_for_place(session, SCHEDULE_RACE_AP, pos),
                rank=pos,
                event_id=int(event.id),
                circuit_id=int(circuit.id),
                source_ref=f"formula:event:{event.id}:pos:{pos}:ap",
            )
            _upsert_ap_suggestion(
                session,
                scope="race",
                currency="channel_points",
                driver_name=driver,
                amount=amount_for_place(session, SCHEDULE_RACE_CP, pos),
                rank=pos,
                event_id=int(event.id),
                circuit_id=int(circuit.id),
                source_ref=f"formula:event:{event.id}:pos:{pos}:cp",
            )
            results_written += 1
        events_touched += 1
    _mark_batch(
        session,
        filename=path.name,
        kind="race_results",
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
    )
    return {"events": events_touched, "results": results_written, "circuit_id": int(circuit.id)}


def _import_derby_event_results(
    session: Session, path: Path, rows: list[dict[str, str]], stamp: str | None
) -> dict[str, Any]:
    circuit = _active_or_new_circuit(session, stamp=stamp, name="Demolition Circuit")
    # Each export is one night; bump event number from existing count
    next_num = (
        session.scalar(
            select(RacingEvent.event_number)
            .where(RacingEvent.circuit_id == int(circuit.id))
            .order_by(RacingEvent.event_number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    # Prefer stamp-stable event: one import file = one event keyed by stamp
    event = None
    if stamp:
        event = session.scalar(
            select(RacingEvent).where(
                RacingEvent.circuit_id == int(circuit.id),
                RacingEvent.export_stamp == stamp,
            ).limit(1)
        )
    if event is None:
        event = _get_or_create_event(
            session,
            circuit,
            event_number=next_num if not stamp else next_num,
            event_kind="heat_night",
            track_name=None,
            stamp=stamp,
        )
        event.title = f"Derby Night {event.event_number}"
    for old in list(session.scalars(select(RacingEventResult).where(RacingEventResult.event_id == int(event.id)))):
        session.delete(old)
    session.flush()
    results_written = 0
    for row in rows:
        pos = cell_int(row, "position", default=0)
        if pos <= 0:
            continue
        driver = row.get("driver") or f"Driver {pos}"
        racer = resolve_racer_by_name(session, driver)
        session.add(
            RacingEventResult(
                event_id=int(event.id),
                racer_id=int(racer.id) if racer else None,
                position=pos,
                car_number=cell_int(row, "number", default=0) or None,
                driver_name=driver,
                controller=row.get("controller") or None,
                vehicle=row.get("vehicle") or None,
                grade=row.get("grade") or None,
                kills=cell_int(row, "kills"),
                damage_dealt=cell_int(row, "damage_dealt"),
                rounds_survived=cell_int(row, "rounds_survived", "minutes"),
                best_stage=row.get("best_stage") or None,
                summary=row.get("eliminated_reason") or None,
                channel_points=amount_for_place(session, SCHEDULE_RACE_CP, pos),
            )
        )
        _upsert_ap_suggestion(
            session,
            scope="race",
            currency="ap",
            driver_name=driver,
            amount=amount_for_place(session, SCHEDULE_RACE_AP, pos),
            rank=pos,
            event_id=int(event.id),
            circuit_id=int(circuit.id),
            source_ref=f"demolition:event:{event.id}:pos:{pos}:ap",
        )
        _upsert_ap_suggestion(
            session,
            scope="race",
            currency="channel_points",
            driver_name=driver,
            amount=amount_for_place(session, SCHEDULE_RACE_CP, pos),
            rank=pos,
            event_id=int(event.id),
            circuit_id=int(circuit.id),
            source_ref=f"demolition:event:{event.id}:pos:{pos}:cp",
        )
        results_written += 1
    _mark_batch(
        session,
        filename=path.name,
        kind="event_results",
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
        event_id=int(event.id),
    )
    return {"event_id": int(event.id), "results": results_written, "circuit_id": int(circuit.id)}


def _import_circuit_standings(
    session: Session,
    path: Path,
    rows: list[dict[str, str]],
    stamp: str | None,
    *,
    kind: str,
) -> dict[str, Any]:
    circuit = _active_or_new_circuit(session, stamp=stamp)
    for old in list(
        session.scalars(select(RacingCircuitStanding).where(RacingCircuitStanding.circuit_id == int(circuit.id)))
    ):
        session.delete(old)
    session.flush()
    written = 0
    for row in rows:
        driver = row.get("driver") or ""
        if not driver:
            continue
        key = normalize_name_key(driver)
        racer = resolve_racer_by_name(session, driver)
        rank = cell_int(row, "rank", default=0)
        points = cell_int(row, "points", "circuit_points")
        session.add(
            RacingCircuitStanding(
                circuit_id=int(circuit.id),
                racer_id=int(racer.id) if racer else None,
                driver_key=key,
                driver_name=driver,
                rank=rank,
                points=points,
                action_points=amount_for_place(session, SCHEDULE_CIRCUIT_AP, rank)
                if rank
                else cell_int(row, "action_points", "ap", "ap_awarded"),
                events_played=cell_int(row, "races", "events"),
                wins=cell_int(row, "wins"),
                kills=cell_int(row, "kills"),
                best_finish=cell_int(row, "best_finish", default=0) or None,
                average_finish=cell_float(row, "average_finish"),
                grade=row.get("grade") or None,
                channel_points=cell_int(row, "channel_points")
                or amount_for_place(session, SCHEDULE_CIRCUIT_CP, rank),
            )
        )
        if rank > 0:
            _upsert_ap_suggestion(
                session,
                scope="circuit",
                currency="ap",
                driver_name=driver,
                amount=amount_for_place(session, SCHEDULE_CIRCUIT_AP, rank),
                rank=rank,
                event_id=None,
                circuit_id=int(circuit.id),
                source_ref=f"circuit:{circuit.id}:rank:{rank}:ap",
            )
            _upsert_ap_suggestion(
                session,
                scope="circuit",
                currency="channel_points",
                driver_name=driver,
                amount=amount_for_place(session, SCHEDULE_CIRCUIT_CP, rank),
                rank=rank,
                event_id=None,
                circuit_id=int(circuit.id),
                source_ref=f"circuit:{circuit.id}:rank:{rank}:cp",
            )
        written += 1
    _mark_batch(
        session,
        filename=path.name,
        kind=kind,
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
    )
    return {"standings": written, "circuit_id": int(circuit.id)}


def _import_channel_points(
    session: Session, path: Path, rows: list[dict[str, str]], stamp: str | None
) -> dict[str, Any]:
    circuit = _active_or_new_circuit(session, stamp=stamp)
    updated = 0
    for row in rows:
        driver = row.get("driver") or ""
        if not driver:
            continue
        key = normalize_name_key(driver)
        standing = session.scalar(
            select(RacingCircuitStanding).where(
                RacingCircuitStanding.circuit_id == int(circuit.id),
                RacingCircuitStanding.driver_key == key,
            ).limit(1)
        )
        pts = cell_int(row, "channel_points")
        if standing is None:
            racer = resolve_racer_by_name(session, driver)
            standing = RacingCircuitStanding(
                circuit_id=int(circuit.id),
                racer_id=int(racer.id) if racer else None,
                driver_key=key,
                driver_name=driver,
                rank=cell_int(row, "rank"),
                points=0,
                channel_points=pts,
            )
            session.add(standing)
        else:
            standing.channel_points = pts
            if cell_int(row, "rank"):
                standing.rank = cell_int(row, "rank")
        updated += 1
    _mark_batch(
        session,
        filename=path.name,
        kind="channel_points",
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
    )
    return {"channel_rows": updated, "circuit_id": int(circuit.id)}


def _import_viewer_finish_awards(
    session: Session, path: Path, rows: list[dict[str, str]], stamp: str | None
) -> dict[str, Any]:
    """Store Formula claimed-finish CSV; race AP/CP come from admin reward schedules on race_results."""
    circuit = _active_or_new_circuit(session, stamp=stamp)
    event = session.scalar(
        select(RacingEvent)
        .where(RacingEvent.circuit_id == int(circuit.id))
        .order_by(RacingEvent.event_number.desc())
        .limit(1)
    )
    _mark_batch(
        session,
        filename=path.name,
        kind="viewer_finish_awards",
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
        event_id=int(event.id) if event else None,
        notes="stored-only; AP/CP from admin race schedules",
    )
    return {"stored_only": True, "rows": len(rows), "circuit_id": int(circuit.id)}


def _import_circuit_ap_awards(
    session: Session, path: Path, rows: list[dict[str, str]], stamp: str | None
) -> dict[str, Any]:
    """End-of-circuit CSV: apply admin circuit AP (+ CP) schedules by rank."""
    circuit = _active_or_new_circuit(session, stamp=stamp)
    count = 0
    for row in rows:
        driver = row.get("driver") or ""
        rank = cell_int(row, "rank", default=0)
        if not driver or rank <= 0:
            continue
        _upsert_ap_suggestion(
            session,
            scope="circuit",
            currency="ap",
            driver_name=driver,
            amount=amount_for_place(session, SCHEDULE_CIRCUIT_AP, rank),
            rank=rank,
            event_id=None,
            circuit_id=int(circuit.id),
            source_ref=f"circuit:{circuit.id}:rank:{rank}:ap",
        )
        _upsert_ap_suggestion(
            session,
            scope="circuit",
            currency="channel_points",
            driver_name=driver,
            amount=amount_for_place(session, SCHEDULE_CIRCUIT_CP, rank),
            rank=rank,
            event_id=None,
            circuit_id=int(circuit.id),
            source_ref=f"circuit:{circuit.id}:rank:{rank}:cp",
        )
        count += 1
    _mark_batch(
        session,
        filename=path.name,
        kind="circuit_ap_awards",
        stamp=stamp,
        row_count=len(rows),
        circuit_id=int(circuit.id),
    )
    return {"circuit_ap": count, "circuit_id": int(circuit.id)}


def import_csv_file(session: Session, path: Path, *, league_slug: str) -> dict[str, Any]:
    ensure_default_reward_tiers(session, league_slug=league_slug)
    kind = classify_export_filename(path.name)
    if kind is None:
        return {"skipped": True, "reason": "unknown_kind", "file": path.name}
    rows = read_csv_dicts(path)
    stamp = parse_export_stamp(path.name)
    detail: dict[str, Any]
    if kind == "race_results":
        detail = _import_formula_race_results(session, path, rows, stamp)
    elif kind == "event_results":
        detail = _import_derby_event_results(session, path, rows, stamp)
    elif kind in ("circuit_standings", "season_standings"):
        detail = _import_circuit_standings(session, path, rows, stamp, kind=kind)
    elif kind == "channel_points":
        detail = _import_channel_points(session, path, rows, stamp)
    elif kind == "viewer_finish_awards":
        detail = _import_viewer_finish_awards(session, path, rows, stamp)
    elif kind == "circuit_ap_awards":
        detail = _import_circuit_ap_awards(session, path, rows, stamp)
    elif kind in (
        "qualifying_standings",
        "race_channel_points",
        "viewer_credit_ledger",
        "kill_awards",
        "viewer_credits",
    ):
        _mark_batch(session, filename=path.name, kind=kind, stamp=stamp, row_count=len(rows), notes="stored-only")
        detail = {"stored_only": True, "rows": len(rows)}
    else:
        detail = {"skipped": True, "reason": "unhandled", "file": path.name}
    detail.update({"file": path.name, "kind": kind})
    return detail


def _ensure_roster_txt_in_raw_dir(raw_dir: Path, *, league_slug: str) -> Path | None:
    """Prefer ``raw_dir/roster.txt``; otherwise copy from the Desktop game path when present."""
    import shutil

    from app.services.racing_racers import default_roster_txt_path

    dest = raw_dir / "roster.txt"
    if dest.is_file():
        return dest
    live = default_roster_txt_path(league_slug)
    if live.is_file():
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, dest)
        return dest
    return None


def import_all_from_raw_dir(session: Session, *, league_slug: str | None = None) -> list[dict[str, Any]]:
    slug = league_slug or str(current_app.config.get("LEAGUE_SLUG") or "")
    ensure_default_reward_tiers(session, league_slug=slug)
    raw_dir = Path(current_app.config["RAW_IMPORT_DIR"])
    results: list[dict[str, Any]] = []

    # Roster first so CSV driver/controller names resolve to racers.
    from app.services.racing_racers import link_roster_txt

    roster_path = _ensure_roster_txt_in_raw_dir(raw_dir, league_slug=slug)
    if roster_path is not None:
        try:
            stats = link_roster_txt(session, roster_path, create_unmatched=True)
            results.append(
                {
                    "kind": "roster",
                    "file": roster_path.name,
                    "entries": stats.get("entries"),
                    "linked": stats.get("linked"),
                    "created": stats.get("created"),
                    "aliased": stats.get("aliased"),
                    "conflicts": stats.get("conflicts") or [],
                }
            )
        except Exception as exc:
            results.append({"kind": "roster", "file": "roster.txt", "error": str(exc)})

    # Process standings after results when possible
    files = list_export_csvs(raw_dir)
    priority = {
        "race_results": 10,
        "event_results": 10,
        "viewer_finish_awards": 20,
        "circuit_standings": 30,
        "season_standings": 30,
        "channel_points": 40,
        "circuit_ap_awards": 50,
    }
    files.sort(key=lambda p: (priority.get(classify_export_filename(p.name) or "", 99), p.name))
    for path in files:
        results.append(import_csv_file(session, path, league_slug=slug))
    return results
