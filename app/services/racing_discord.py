"""Enqueue Discord posts for racing race/heat results and circuit standings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.racing_models import RacingCircuit, RacingCircuitStanding, RacingEvent, RacingEventResult
from app.services.discord_events import enqueue_discord_event, enqueue_repeatable_discord_event


def enqueue_event_results_discord(session: Session, *, league_slug: str, event_id: int) -> None:
    event = session.get(RacingEvent, int(event_id))
    if event is None:
        return
    results = list(
        session.scalars(
            select(RacingEventResult)
            .where(RacingEventResult.event_id == int(event_id))
            .order_by(RacingEventResult.position.asc())
        ).all()
    )
    if not results:
        return
    is_derby = str(event.event_kind or "") == "heat_night" or league_slug == "bowl-demolition"
    event_key = "heat_results" if is_derby else "race_results"
    title = event.title or event.track_name or f"Event {event.event_number}"
    rows = []
    for r in results[:25]:
        rows.append(
            {
                "position": r.position,
                "driver": r.driver_name,
                "controller": r.controller or "",
                "points": r.circuit_points,
                "channel_points": r.channel_points,
                "kills": r.kills,
                "vehicle": r.vehicle or "",
            }
        )
    payload = {
        "title": title,
        "event_number": event.event_number,
        "track": event.track_name or "",
        "event_kind": event.event_kind,
        "rows": rows,
        "url_path": f"/results/{event.id}",
    }
    enqueue_discord_event(
        session,
        league_slug=league_slug,
        event_key=event_key,
        source_type="racing_event",
        source_id=str(event.id),
        payload=payload,
        created_by_user_id=None,
    )


def enqueue_circuit_standings_discord(session: Session, *, league_slug: str, circuit_id: int) -> None:
    circuit = session.get(RacingCircuit, int(circuit_id))
    if circuit is None:
        return
    standings = list(
        session.scalars(
            select(RacingCircuitStanding)
            .where(RacingCircuitStanding.circuit_id == int(circuit_id))
            .order_by(RacingCircuitStanding.rank.asc(), RacingCircuitStanding.points.desc())
        ).all()
    )
    if not standings:
        return
    rows = []
    for s in standings[:20]:
        rows.append(
            {
                "rank": s.rank,
                "driver": s.driver_name,
                "points": s.points,
                "channel_points": s.channel_points,
                "wins": s.wins,
                "kills": s.kills,
                "events": s.events_played,
                "action_points": s.action_points,
            }
        )
    payload = {
        "title": f"{circuit.name} standings",
        "circuit_id": circuit.id,
        "circuit_name": circuit.name,
        "rows": rows,
        "url_path": "/circuit",
    }
    enqueue_repeatable_discord_event(
        session,
        league_slug=league_slug,
        event_key="circuit_standings_update",
        payload=payload,
        created_by_user_id=None,
        idempotency_key=f"racing-circuit-{league_slug}-{circuit.id}",
    )


def enqueue_after_import(
    session: Session,
    *,
    league_slug: str,
    import_results: list[dict],
) -> None:
    event_ids: set[int] = set()
    circuit_ids: set[int] = set()
    for detail in import_results:
        if detail.get("event_id"):
            event_ids.add(int(detail["event_id"]))
        if detail.get("circuit_id"):
            circuit_ids.add(int(detail["circuit_id"]))
        # Formula race_results may touch multiple events without listing ids — refresh latest
        if detail.get("kind") == "race_results" and detail.get("circuit_id"):
            cid = int(detail["circuit_id"])
            latest = session.scalar(
                select(RacingEvent)
                .where(RacingEvent.circuit_id == cid)
                .order_by(RacingEvent.event_number.desc())
                .limit(1)
            )
            if latest is not None:
                event_ids.add(int(latest.id))
    for eid in event_ids:
        enqueue_event_results_discord(session, league_slug=league_slug, event_id=eid)
    for cid in circuit_ids:
        enqueue_circuit_standings_discord(session, league_slug=league_slug, circuit_id=cid)
