"""Grant racing reward suggestions (AP → Cap/Hist ledger; CP → mark paid)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.racing_models import RacingApSuggestion, RacingRacer
from app.services.ap_service import add_ledger_entry
from app.sqlite_retry import commit_with_sqlite_retry


def pending_suggestions(
    session: Session,
    *,
    scope: str | None = None,
    currency: str | None = None,
    event_id: int | None = None,
    circuit_id: int | None = None,
) -> list[RacingApSuggestion]:
    q = select(RacingApSuggestion).where(RacingApSuggestion.status == "pending")
    if scope:
        q = q.where(RacingApSuggestion.scope == scope)
    if currency:
        q = q.where(RacingApSuggestion.currency == currency)
    if event_id is not None:
        q = q.where(RacingApSuggestion.event_id == int(event_id))
    if circuit_id is not None:
        q = q.where(RacingApSuggestion.circuit_id == int(circuit_id))
    return list(session.scalars(q.order_by(RacingApSuggestion.rank.asc(), RacingApSuggestion.id)).all())


def grant_suggestion_batch(
    session: Session,
    suggestion_ids: list[int],
    *,
    destination_league_slug: str | None,
    created_by_user_id: int | None,
    racing_league_slug: str,
) -> dict[str, int]:
    """Apply pending suggestions.

    - ``currency=ap``: write Cap/Historical ledger (destination required).
    - ``currency=channel_points``: mark paid for Twitch host payout tracking (no ledger).
    """
    granted = 0
    skipped = 0
    blocked = 0

    rows = list(
        session.scalars(
            select(RacingApSuggestion).where(RacingApSuggestion.id.in_([int(i) for i in suggestion_ids]))
        ).all()
    )
    for sug in rows:
        if sug.status == "granted":
            skipped += 1
            continue
        if int(sug.amount or 0) <= 0:
            sug.status = "skipped"
            skipped += 1
            continue

        currency = str(sug.currency or "ap").strip() or "ap"
        if currency == "channel_points":
            sug.status = "granted"
            sug.granted_at = datetime.utcnow()
            sug.source_ref = sug.source_ref or f"{racing_league_slug}:{sug.scope}:{sug.id}:cp"
            granted += 1
            continue

        if destination_league_slug not in ("bowl-cap", "bowl-historical"):
            raise ValueError("Destination must be bowl-cap or bowl-historical for AP grants")

        racer: RacingRacer | None = None
        if sug.racer_id:
            racer = session.get(RacingRacer, int(sug.racer_id))
        team_id = None
        if racer is not None:
            if racer.ap_league_slug == destination_league_slug and racer.ap_team_id:
                team_id = int(racer.ap_team_id)
            else:
                from app.site_models import GmLeagueMembership

                if racer.user_id:
                    m = session.scalar(
                        select(GmLeagueMembership)
                        .where(
                            GmLeagueMembership.user_id == int(racer.user_id),
                            GmLeagueMembership.league_slug == destination_league_slug,
                            GmLeagueMembership.status == "active",
                        )
                        .limit(1)
                    )
                    if m is not None:
                        team_id = int(m.team_id)
        if team_id is None:
            blocked += 1
            continue

        source_ref = sug.source_ref or f"{racing_league_slug}:{sug.scope}:{sug.id}:ap"
        entry = add_ledger_entry(
            league_slug=destination_league_slug,
            team_id=team_id,
            delta=int(sug.amount),
            reason_code=f"racing_{sug.scope}_ap",
            meta={
                "racing_league": racing_league_slug,
                "driver": sug.driver_name,
                "scope": sug.scope,
                "rank": sug.rank,
                "suggestion_id": sug.id,
                "currency": currency,
            },
            created_by_user_id=created_by_user_id,
            source_ref=source_ref,
        )
        if entry is None:
            sug.status = "granted"
            sug.granted_league_slug = destination_league_slug
            sug.granted_team_id = team_id
            sug.granted_at = datetime.utcnow()
            skipped += 1
            continue
        sug.status = "granted"
        sug.granted_league_slug = destination_league_slug
        sug.granted_team_id = team_id
        sug.granted_at = datetime.utcnow()
        sug.source_ref = source_ref
        granted += 1

    commit_with_sqlite_retry(session)
    return {"granted": granted, "skipped": skipped, "blocked": blocked}
