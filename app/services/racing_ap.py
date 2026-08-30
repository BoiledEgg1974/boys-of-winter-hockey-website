"""Grant racing reward suggestions (AP → Cap/Hist ledger; CP → mark paid)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.racing_models import RacingApSuggestion, RacingRacer
from app.services.ap_service import add_ledger_entry
from app.site_models import GmLeagueMembership
from app.sqlite_retry import commit_with_sqlite_retry

_AP_LEAGUES = ("bowl-cap", "bowl-historical")


def _active_membership(session: Session, user_id: int, league_slug: str) -> GmLeagueMembership | None:
    return session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.user_id == int(user_id),
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
        .limit(1)
    )


def resolve_grant_target(
    session: Session,
    racer: RacingRacer | None,
    destination_league_slug: str,
) -> tuple[str, int] | None:
    """Pick Cap/Historical team for an AP grant.

    Prefer the batch destination when the racer is mapped there; otherwise use
    the racer's own AP target or an active membership so linked drivers still
    leave the pending grants list.
    """
    if racer is None:
        return None
    dest = str(destination_league_slug or "").strip()
    if dest in _AP_LEAGUES and racer.ap_league_slug == dest and racer.ap_team_id:
        return dest, int(racer.ap_team_id)
    if dest in _AP_LEAGUES and racer.user_id:
        membership = _active_membership(session, int(racer.user_id), dest)
        if membership is not None:
            return dest, int(membership.team_id)
    if racer.ap_league_slug in _AP_LEAGUES and racer.ap_team_id:
        return str(racer.ap_league_slug), int(racer.ap_team_id)
    if racer.user_id:
        for slug in _AP_LEAGUES:
            if slug == dest:
                continue
            membership = _active_membership(session, int(racer.user_id), slug)
            if membership is not None:
                return slug, int(membership.team_id)
    return None


def _mark_granted(
    sug: RacingApSuggestion,
    *,
    league_slug: str | None = None,
    team_id: int | None = None,
    source_ref: str | None = None,
) -> None:
    sug.status = "granted"
    sug.granted_at = datetime.utcnow()
    if league_slug:
        sug.granted_league_slug = league_slug
    if team_id is not None:
        sug.granted_team_id = int(team_id)
    if source_ref:
        sug.source_ref = source_ref


def pending_suggestions(
    session: Session,
    *,
    scope: str | None = None,
    currency: str | None = None,
    event_id: int | None = None,
    circuit_id: int | None = None,
) -> list[RacingApSuggestion]:
    q = select(RacingApSuggestion).options(selectinload(RacingApSuggestion.event)).where(
        RacingApSuggestion.status == "pending"
    )
    if scope:
        q = q.where(RacingApSuggestion.scope == scope)
    if currency:
        q = q.where(RacingApSuggestion.currency == currency)
    if event_id is not None:
        q = q.where(RacingApSuggestion.event_id == int(event_id))
    if circuit_id is not None:
        q = q.where(RacingApSuggestion.circuit_id == int(circuit_id))
    return list(
        session.scalars(
            q.order_by(
                RacingApSuggestion.event_id.asc(),
                RacingApSuggestion.rank.asc(),
                RacingApSuggestion.id,
            )
        ).all()
    )


def suggestion_event_label(sug: RacingApSuggestion) -> str:
    event = getattr(sug, "event", None)
    if event is not None:
        title = (event.title or event.track_name or "").strip()
        number = int(event.event_number or 0)
        if title and number:
            return f"Race {number} · {title}"
        if title:
            return title
        if number:
            return f"Race {number}"
    if sug.scope == "circuit":
        return "Circuit"
    return "—"


def dismiss_suggestion_batch(session: Session, suggestion_ids: list[int]) -> dict[str, int]:
    """Remove already-paid rows from the pending list without writing AP again."""
    dismissed = 0
    skipped = 0
    rows = list(
        session.scalars(
            select(RacingApSuggestion).where(RacingApSuggestion.id.in_([int(i) for i in suggestion_ids]))
        ).all()
    )
    for sug in rows:
        if sug.status == "granted":
            skipped += 1
            continue
        _mark_granted(sug)
        dismissed += 1
    commit_with_sqlite_retry(session)
    return {"dismissed": dismissed, "skipped": skipped}


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
            _mark_granted(
                sug,
                source_ref=sug.source_ref or f"{racing_league_slug}:{sug.scope}:{sug.id}:cp",
            )
            granted += 1
            continue

        if destination_league_slug not in _AP_LEAGUES:
            raise ValueError("Destination must be bowl-cap or bowl-historical for AP grants")

        racer: RacingRacer | None = None
        if sug.racer_id:
            racer = session.get(RacingRacer, int(sug.racer_id))
        target = resolve_grant_target(session, racer, destination_league_slug)
        if target is None:
            blocked += 1
            continue

        league_slug, team_id = target
        source_ref = sug.source_ref or f"{racing_league_slug}:{sug.scope}:{sug.id}:ap"
        entry = add_ledger_entry(
            league_slug=league_slug,
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
        _mark_granted(sug, league_slug=league_slug, team_id=team_id, source_ref=source_ref)
        if entry is None:
            skipped += 1
            continue
        granted += 1

    commit_with_sqlite_retry(session)
    return {"granted": granted, "skipped": skipped, "blocked": blocked}
