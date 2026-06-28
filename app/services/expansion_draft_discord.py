"""Expansion Draft Hub Discord channel payloads, enqueue helpers, and slash status."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.expansion_draft_state import featured_expansion_draft, slots_ordered
from app.services.gm_messaging import gm_discord_name
from app.services.player_ratings_csv import player_positions_display_label
from app.site_models import LeagueExpansionDraft, LeagueExpansionDraftPick, LeagueExpansionDraftSlot, User


def _phase_label(phase: str | None) -> str:
    ph = str(phase or "").strip()
    if not ph:
        return ""
    return ph[0].upper() + ph[1:] if len(ph) > 1 else ph.upper()


def gm_discord_mentions(session: Session, league_slug: str, team_id: int) -> str:
    """Discord mention string for active GMs on a franchise."""
    from app.services.expansion_draft_state import gm_user_ids_for_team

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


def expansion_draft_on_clock_payload(
    session: Session,
    draft: LeagueExpansionDraft,
    slot: LeagueExpansionDraftSlot,
    *,
    team_fields: dict | None = None,
) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url, team_fields_for_discord

    tm = session.get(Team, int(slot.team_id))
    tf = dict(team_fields or team_fields_for_discord(tm))
    team_name = tf.get("team_name") or (tm.full_display_name() if tm else f"Team #{slot.team_id}")
    phase = _phase_label(str(slot.phase or ""))
    return {
        **tf,
        "draft_id": int(draft.id),
        "draft_name": str(draft.name or "Expansion draft"),
        "team_name": team_name,
        "round": int(slot.round),
        "phase": phase,
        "overall_pick": int(slot.overall_pick),
        "gm_mentions": gm_discord_mentions(session, draft.league_slug, int(slot.team_id)),
        "url": build_league_public_url(draft.league_slug, f"/expansion-draft-hub/{int(draft.id)}"),
        "has_image": False,
    }


def expansion_draft_completed_payload(
    session: Session,
    draft: LeagueExpansionDraft,
    *,
    ended_early: bool = False,
) -> dict[str, Any]:
    from app.services.discord_events import build_league_public_url

    picks = list(
        session.scalars(
            select(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
            .order_by(LeagueExpansionDraftPick.overall_pick.asc())
        ).all()
    )
    lines: list[str] = []
    for pk in picks[:12]:
        tm = session.get(Team, int(pk.team_id))
        pl = session.get(Player, int(pk.player_id))
        team_lbl = tm.full_display_name() if tm else str(pk.team_id)
        ply = pl.full_name if pl else str(pk.player_id)
        ph = _phase_label(str(pk.phase or ""))
        ph_bit = f" [{ph}]" if ph else ""
        lines.append(f"#{pk.overall_pick} · {team_lbl}: {ply}{ph_bit}")
    hub_url = build_league_public_url(draft.league_slug, f"/expansion-draft-hub/{int(draft.id)}")
    return {
        "draft_id": int(draft.id),
        "draft_name": str(draft.name or "Expansion draft"),
        "pick_count": len(picks),
        "recap_lines": lines,
        "body": "\n".join(lines) if lines else "Expansion draft complete.",
        "ended_early": bool(ended_early),
        "url": hub_url,
        "has_image": False,
    }


def enqueue_expansion_draft_discord_alerts(
    session: Session,
    draft: LeagueExpansionDraft,
    current_slot: LeagueExpansionDraftSlot,
) -> None:
    """Public #expansion-draft alert when a new pick clock starts."""
    from app.services.discord_events import enqueue_discord_event

    if draft.status != "live":
        return
    ov = int(current_slot.overall_pick)
    enqueue_discord_event(
        session,
        league_slug=draft.league_slug,
        event_key="expansion_draft_on_clock",
        payload=expansion_draft_on_clock_payload(session, draft, current_slot),
        created_by_user_id=None,
        source_type="expansion_draft_on_clock",
        source_id=f"{int(draft.id)}:{ov}",
    )


def enqueue_expansion_draft_completed(
    session: Session,
    draft: LeagueExpansionDraft,
    *,
    ended_early: bool = False,
) -> None:
    from app.services.discord_events import enqueue_discord_event

    enqueue_discord_event(
        session,
        league_slug=draft.league_slug,
        event_key="expansion_draft_completed",
        payload=expansion_draft_completed_payload(session, draft, ended_early=ended_early),
        created_by_user_id=None,
        source_type="expansion_draft_completed",
        source_id=str(int(draft.id)),
    )


def build_expansion_status_message(session: Session, league_slug: str) -> str:
    """Ephemeral text for /expansionstatus slash command."""
    draft = featured_expansion_draft(session, league_slug)
    if draft is None or draft.status != "live":
        return "No live Expansion Draft for this league right now."
    slots = slots_ordered(session, draft.id)
    lines = [f"**{draft.name}** — live"]
    if draft.awaiting_admin_resolution:
        lines.append("Status: waiting for commissioner to resolve the current pick.")
    elif draft.current_slot_index < len(slots):
        lines.append("Status: on the clock; waiting for commissioner to record the pick.")
    else:
        lines.append("Status: between picks.")
    if draft.current_slot_index < len(slots):
        cs = slots[draft.current_slot_index]
        if not cs.forfeited:
            tm = session.get(Team, int(cs.team_id))
            team_lbl = tm.full_display_name() if tm else str(cs.team_id)
            phase = _phase_label(str(cs.phase or ""))
            ph_bit = f" · {phase} phase" if phase else ""
            lines.append(
                f"On the clock: {team_lbl} — Round {cs.round}{ph_bit} (overall #{cs.overall_pick})"
            )
    recent = list(
        session.scalars(
            select(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
            .order_by(LeagueExpansionDraftPick.overall_pick.desc())
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

    url = build_league_public_url(league_slug, f"/expansion-draft-hub/{int(draft.id)}")
    if url:
        lines.append("")
        lines.append(url)
    return "\n".join(lines)[:1900]
