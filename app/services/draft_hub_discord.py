"""Draft Hub Discord channel payloads, enqueue helpers, and slash status."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.draft_hub_state import featured_draft, gm_user_ids_for_team, slots_ordered, utcnow_naive
from app.services.gm_messaging import gm_discord_name
from app.services.player_ratings_csv import player_positions_display_label
from app.site_models import LeagueDraft, LeagueDraftPick, LeagueDraftSlot, User


def selection_in_round(overall_pick: int, picks_per_round: int) -> int:
    ppr = max(1, int(picks_per_round or 1))
    return ((int(overall_pick) - 1) % ppr) + 1


def gm_discord_mentions(session: Session, league_slug: str, team_id: int) -> str:
    """Discord mention string for active GMs on a franchise."""
    parts: list[str] = []
    for uid in gm_user_ids_for_team(session, league_slug, int(team_id)):
        user = session.get(User, int(uid))
        if user is None:
            continue
        did = str(user.discord_user_id or "").strip()
        if did:
            parts.append(f"<@{did}>")
        else:
            label = gm_discord_name(user)
            if label:
                parts.append(label)
    return " ".join(parts) if parts else "GM"


def _slot_has_pick(session: Session, draft_id: int, overall_pick: int) -> bool:
    return (
        session.scalar(
            select(LeagueDraftPick.id).where(
                LeagueDraftPick.league_draft_id == int(draft_id),
                LeagueDraftPick.overall_pick == int(overall_pick),
            )
        )
        is not None
    )


def _next_unpicked_slot(slots: list[LeagueDraftSlot], start_index: int, draft_id: int, session: Session) -> LeagueDraftSlot | None:
    j = int(start_index) + 1
    while j < len(slots):
        ns = slots[j]
        j += 1
        if ns.forfeited:
            continue
        if _slot_has_pick(session, draft_id, int(ns.overall_pick)):
            continue
        return ns
    return None


def draft_hub_on_clock_payload(
    session: Session,
    draft: LeagueDraft,
    slot: LeagueDraftSlot,
    *,
    team_fields: dict | None = None,
) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url, team_fields_for_discord

    tm = session.get(Team, int(slot.team_id))
    tf = dict(team_fields or team_fields_for_discord(tm))
    team_name = tf.get("team_name") or (tm.full_display_name() if tm else f"Team #{slot.team_id}")
    ppr = max(1, int(getattr(draft, "picks_per_round", 27) or 27))
    sel = selection_in_round(int(slot.overall_pick), ppr)
    timer_sec = max(5, int(draft.timer_seconds or 120))
    timer_mins = max(1, (timer_sec + 59) // 60)
    return {
        **tf,
        "draft_id": int(draft.id),
        "draft_name": str(draft.name or "Draft Hub"),
        "team_name": team_name,
        "round": int(slot.round),
        "selection": sel,
        "overall_pick": int(slot.overall_pick),
        "gm_mentions": gm_discord_mentions(session, draft.league_slug, int(slot.team_id)),
        "timer_minutes": timer_mins,
        "url": build_league_public_url(draft.league_slug, "/draft-hub"),
        "has_image": False,
    }


def draft_hub_on_deck_payload(
    session: Session,
    draft: LeagueDraft,
    slot: LeagueDraftSlot,
    *,
    team_fields: dict | None = None,
) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url, team_fields_for_discord

    tm = session.get(Team, int(slot.team_id))
    tf = dict(team_fields or team_fields_for_discord(tm))
    team_name = tf.get("team_name") or (tm.full_display_name() if tm else f"Team #{slot.team_id}")
    ppr = max(1, int(getattr(draft, "picks_per_round", 27) or 27))
    return {
        **tf,
        "draft_id": int(draft.id),
        "draft_name": str(draft.name or "Draft Hub"),
        "team_name": team_name,
        "round": int(slot.round),
        "selection": selection_in_round(int(slot.overall_pick), ppr),
        "overall_pick": int(slot.overall_pick),
        "gm_mentions": gm_discord_mentions(session, draft.league_slug, int(slot.team_id)),
        "url": build_league_public_url(draft.league_slug, "/draft-hub"),
        "has_image": False,
    }


def draft_hub_completed_payload(session: Session, draft: LeagueDraft) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url

    picks = list(
        session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.asc())
        ).all()
    )
    lines: list[str] = []
    for pk in picks[:12]:
        tm = session.get(Team, int(pk.team_id))
        pl = session.get(Player, int(pk.player_id))
        team_lbl = tm.full_display_name() if tm else str(pk.team_id)
        ply = pl.full_name if pl else str(pk.player_id)
        pos = (player_positions_display_label(pl) or "").strip() if pl else ""
        pos_bit = f" ({pos})" if pos else ""
        lines.append(f"#{pk.overall_pick} · {team_lbl}: {ply}{pos_bit}")
    archive_url = build_league_public_url(draft.league_slug, f"/draft-hub/archive/{int(draft.id)}")
    hub_url = build_league_public_url(draft.league_slug, "/draft-hub")
    return {
        "draft_id": int(draft.id),
        "draft_name": str(draft.name or "Draft Hub"),
        "pick_count": len(picks),
        "recap_lines": lines,
        "body": "\n".join(lines) if lines else "Draft complete.",
        "archive_url": archive_url,
        "url": hub_url,
        "has_image": False,
    }


def enqueue_draft_hub_discord_alerts(
    session: Session,
    draft: LeagueDraft,
    current_slot: LeagueDraftSlot,
    slots: list[LeagueDraftSlot],
) -> None:
    """Public #draft-discussion alerts when a new pick clock starts."""
    from app.services.discord_events import enqueue_discord_event

    if draft.status != "live":
        return
    ov = int(current_slot.overall_pick)
    if int(current_slot.round) <= 2:
        enqueue_discord_event(
            session,
            league_slug=draft.league_slug,
            event_key="draft_hub_on_clock",
            payload=draft_hub_on_clock_payload(session, draft, current_slot),
            created_by_user_id=None,
            source_type="draft_on_clock",
            source_id=f"{int(draft.id)}:{ov}",
        )
    if bool(getattr(draft, "discord_on_deck_enabled", False)):
        next_slot = _next_unpicked_slot(slots, draft.current_slot_index, draft.id, session)
        if next_slot is not None:
            enqueue_discord_event(
                session,
                league_slug=draft.league_slug,
                event_key="draft_hub_on_deck",
                payload=draft_hub_on_deck_payload(session, draft, next_slot),
                created_by_user_id=None,
                source_type="draft_on_deck",
                source_id=f"{int(draft.id)}:{ov}",
            )


def enqueue_draft_hub_completed(session: Session, draft: LeagueDraft) -> None:
    from app.services.discord_events import enqueue_discord_event

    enqueue_discord_event(
        session,
        league_slug=draft.league_slug,
        event_key="draft_hub_completed",
        payload=draft_hub_completed_payload(session, draft),
        created_by_user_id=None,
        source_type="draft_completed",
        source_id=str(int(draft.id)),
    )


def build_draft_status_message(session: Session, league_slug: str) -> str:
    """Ephemeral text for /draftstatus slash command."""
    draft = featured_draft(session, league_slug)
    if draft is None or draft.status != "live":
        return "No live Draft Hub draft for this league right now."
    slots = slots_ordered(session, draft.id)
    lines = [f"**{draft.name}** — live"]
    if draft.awaiting_admin_resolution:
        lines.append("Status: waiting for commissioner to resolve the current pick.")
    elif getattr(draft, "timer_paused", False):
        rem = draft.timer_paused_remaining_seconds or draft.timer_seconds
        lines.append(f"Status: timer paused (~{rem}s remaining).")
    elif draft.pick_deadline_at:
        sec = max(0, int((draft.pick_deadline_at - utcnow_naive()).total_seconds()))
        lines.append(f"Status: on the clock ({sec}s left).")
    else:
        lines.append("Status: between picks.")
    if draft.current_slot_index < len(slots):
        cs = slots[draft.current_slot_index]
        if not cs.forfeited:
            tm = session.get(Team, int(cs.team_id))
            ppr = max(1, int(getattr(draft, "picks_per_round", 27) or 27))
            sel = selection_in_round(int(cs.overall_pick), ppr)
            team_lbl = tm.full_display_name() if tm else str(cs.team_id)
            lines.append(f"On the clock: {team_lbl} — Round {cs.round}, Selection {sel} (overall #{cs.overall_pick})")
    next_slot = _next_unpicked_slot(slots, draft.current_slot_index, draft.id, session)
    if next_slot is not None:
        tm2 = session.get(Team, int(next_slot.team_id))
        ppr = max(1, int(getattr(draft, "picks_per_round", 27) or 27))
        sel2 = selection_in_round(int(next_slot.overall_pick), ppr)
        team_lbl2 = tm2.full_display_name() if tm2 else str(next_slot.team_id)
        lines.append(f"On deck: {team_lbl2} — Round {next_slot.round}, Selection {sel2}")
    recent = list(
        session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.desc())
            .limit(5)
        ).all()
    )
    if recent:
        lines.append("")
        lines.append("Recent picks:")
        for pk in reversed(recent):
            tm = session.get(Team, int(pk.team_id))
            pl = session.get(Player, int(pk.player_id))
            team_lbl = tm.abbreviation if tm and tm.abbreviation else (tm.name if tm else "?")
            ply = pl.full_name if pl else str(pk.player_id)
            lines.append(f"#{pk.overall_pick} {team_lbl}: {ply}")
    from app.services.discord_events import build_league_public_url

    url = build_league_public_url(league_slug, "/draft-hub")
    if url:
        lines.append("")
        lines.append(url)
    return "\n".join(lines)[:1900]
