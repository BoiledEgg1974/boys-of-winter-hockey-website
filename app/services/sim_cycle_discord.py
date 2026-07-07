"""Discord sim cycle export board (#sim-log closed recap only; FTP bot handles live)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team
from app.services.staff_salaries import main_league_teams
from app.services.discord_events import (
    GM_EXPORT_TRACKER_POLL_EVENT_KEY,
    SIM_CYCLE_UPDATE_EVENT_KEY,
    bot_event_delivery_fields,
    enqueue_repeatable_discord_event,
)
from app.site_models import GmExportAttendance, GmLeagueMembership, SimCycleState


def get_or_create_sim_cycle_state(session: Session, league_slug: str) -> SimCycleState:
    slug = str(league_slug or "").strip()
    row = session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    if row is not None:
        return row
    row = SimCycleState(league_slug=slug, phase="idle", live_exported_fhm_team_ids_json="[]")
    session.add(row)
    session.flush()
    return row


def _load_live_exported_fhm_ids(state: SimCycleState) -> set[int]:
    try:
        data = json.loads(str(state.live_exported_fhm_team_ids_json or "[]"))
    except json.JSONDecodeError:
        return set()
    out: set[int] = set()
    if isinstance(data, list):
        for item in data:
            try:
                out.add(int(item))
            except (TypeError, ValueError):
                continue
    return out


def _store_live_exported_fhm_ids(state: SimCycleState, ids: set[int]) -> None:
    state.live_exported_fhm_team_ids_json = json.dumps(sorted(ids))


def _memberships_by_team_id(site_session: Session, league_slug: str) -> dict[int, GmLeagueMembership]:
    memberships = list(
        site_session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    return {int(m.team_id): m for m in memberships}


def _sim_cycle_teams_for_league(league_session: Session) -> list[Team]:
    """All main-league clubs for the sim board (not limited to active GM memberships)."""
    return main_league_teams(league_session)


def _fhm_team_id_for_team(
    team: Team,
    membership: GmLeagueMembership | None,
    *,
    league_slug: str,
) -> int | None:
    """Resolve FHM franchise TeamId (not site teams.id)."""
    from scripts.league_discord_bot.team_maps import teams_for_league_slug

    roster = teams_for_league_slug(league_slug)
    membership_fhm = membership.fhm_team_id if membership is not None else None
    for raw in (team.fhm_team_id, membership_fhm):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            tid = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if tid in roster:
            return tid
    return None


def _abbrev_for_fhm_id(league_slug: str, fhm_team_id: int) -> str:
    from scripts.league_discord_bot.team_maps import teams_for_league_slug

    entry = teams_for_league_slug(league_slug).get(int(fhm_team_id))
    return str(entry[0] or "").strip().upper() if entry else ""


def build_export_team_lists(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    exported_fhm_team_ids: set[int],
) -> dict[str, list[int]]:
    """Exported vs pending FHM team ids for the sim-log board (league-wide, sorted by abbrev)."""
    memberships_by_team_id = _memberships_by_team_id(site_session, league_slug)
    exported: list[int] = []
    pending: list[int] = []

    for team in _sim_cycle_teams_for_league(league_session):
        mem = memberships_by_team_id.get(int(team.id))
        fhm_id = _fhm_team_id_for_team(team, mem, league_slug=league_slug)
        if fhm_id is None:
            continue
        if int(fhm_id) in exported_fhm_team_ids:
            exported.append(int(fhm_id))
        else:
            pending.append(int(fhm_id))

    sort_key = lambda tid: _abbrev_for_fhm_id(league_slug, int(tid))
    return {
        "exported": sorted(exported, key=sort_key),
        "pending": sorted(pending, key=sort_key),
    }


def _closed_exported_fhm_team_ids(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> set[int]:
    rows = list(
        site_session.scalars(
            select(GmExportAttendance).where(
                GmExportAttendance.league_slug == league_slug,
                GmExportAttendance.export_date == export_date,
            )
        ).all()
    )
    if not rows:
        return set()
    team_ids = {int(r.team_id) for r in rows}
    teams = {
        int(t.id): t
        for t in league_session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    }
    memberships = _memberships_by_team_id(site_session, league_slug)
    exported: set[int] = set()
    for tid in team_ids:
        team = teams.get(tid)
        if team is None:
            continue
        mem = memberships.get(tid)
        fhm_id = _fhm_team_id_for_team(team, mem, league_slug=league_slug)
        if fhm_id is not None:
            exported.add(int(fhm_id))
    return exported


def _current_sim_log_channel_id(session: Session, league_slug: str) -> str:
    delivery = bot_event_delivery_fields(
        session,
        league_slug=league_slug,
        event_key=SIM_CYCLE_UPDATE_EVENT_KEY,
    )
    return str(delivery.get("discord_channel_id") or "").strip()


def _tracker_channel_id(session: Session, league_slug: str) -> str:
    """#gm-export-tracker snowflake — live sim-log counts come from this channel only."""
    from app.services.discord_events import resolve_discord_channel_id

    return resolve_discord_channel_id(
        session,
        league_slug=league_slug,
        event_key=GM_EXPORT_TRACKER_POLL_EVENT_KEY,
        channel_key="gm-export-tracker",
    )


def _message_id_for_channel(
    message_id: str | None,
    stored_channel_id: str | None,
    current_channel_id: str,
) -> str | None:
    mid = str(message_id or "").strip()
    stored = str(stored_channel_id or "").strip()
    if not mid or not current_channel_id or not stored:
        return None
    if stored != current_channel_id:
        return None
    return mid


def resolve_sim_cycle_discord_message_id(session: Session, league_slug: str) -> str | None:
    state = session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == league_slug).limit(1)
    )
    if state is None:
        return None
    current_channel = _current_sim_log_channel_id(session, league_slug)
    return _message_id_for_channel(
        state.discord_message_id,
        state.discord_channel_id,
        current_channel,
    )


def _payload_content_hash(payload: dict[str, Any]) -> str:
    stable = {
        k: v
        for k, v in payload.items()
        if k not in {"last_updated_at", "content_hash"}
    }
    blob = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:64]


def build_sim_cycle_discord_payload(
    site_session: Session,
    league_session: Session,
    state: SimCycleState,
    *,
    post_new_message: bool = False,
    finalize_on_ack: bool = False,
) -> dict[str, Any]:
    league_slug = str(state.league_slug or "")
    phase = str(state.phase or "idle")
    export_date = state.export_date
    now = datetime.utcnow()
    from scripts.league_discord_bot.team_maps import sim_cycle_embed_color

    if phase == "closed" and export_date is not None:
        exported_ids = _closed_exported_fhm_team_ids(
            site_session, league_session, league_slug, export_date
        )
    else:
        exported_ids = _load_live_exported_fhm_ids(state)

    team_lists = build_export_team_lists(
        site_session, league_session, league_slug, exported_ids
    )
    exported_list = team_lists["exported"]
    pending_list = team_lists["pending"]
    total_teams = len(exported_list) + len(pending_list)
    exported_count = len(exported_ids)

    phase_label = "live" if phase == "live" else "closed" if phase == "closed" else "idle"
    payload: dict[str, Any] = {
        "title": f"Current Sim Cycle ({phase_label})",
        "phase": phase,
        "export_date": export_date.isoformat() if export_date else "",
        "exported": exported_list,
        "pending": pending_list,
        "exported_fhm_team_ids": sorted(exported_ids),
        "total_teams": total_teams,
        "exported_count": exported_count,
        "last_updated_at": now.isoformat(),
        "embed_color": sim_cycle_embed_color(league_slug),
        "source_type": "sim_cycle_state",
        "source_id": league_slug,
        "content_hash": "",
        "finalize_on_ack": bool(finalize_on_ack),
    }
    payload["content_hash"] = _payload_content_hash(payload)

    if not post_new_message:
        edit_id = resolve_sim_cycle_discord_message_id(site_session, league_slug)
        if edit_id:
            payload["edit_message_id"] = edit_id
    else:
        payload["post_new_message"] = True

    return payload


def maybe_enqueue_sim_cycle_discord(
    site_session: Session,
    league_session: Session,
    state: SimCycleState,
    *,
    force: bool = False,
    post_new_message: bool = False,
    finalize_on_ack: bool = False,
) -> bool:
    # News-bot posts closed recaps only; the FTP bot owns live #sim-log updates.
    if str(state.phase or "") != "closed":
        return False
    payload = build_sim_cycle_discord_payload(
        site_session,
        league_session,
        state,
        post_new_message=post_new_message,
        finalize_on_ack=finalize_on_ack,
    )
    content_hash = str(payload.get("content_hash") or "")
    prev_hash = str(state.discord_payload_hash or "").strip()
    if not force and content_hash and content_hash == prev_hash:
        return False
    if finalize_on_ack:
        state.finalize_on_ack = True
    row = enqueue_repeatable_discord_event(
        site_session,
        league_slug=str(state.league_slug or ""),
        event_key=SIM_CYCLE_UPDATE_EVENT_KEY,
        payload=payload,
        created_by_user_id=None,
    )
    if row is not None and content_hash:
        state.discord_payload_hash = content_hash
        state.updated_at = datetime.utcnow()
    return row is not None


def reset_sim_cycle_state(session: Session, league_slug: str) -> SimCycleState:
    state = get_or_create_sim_cycle_state(session, league_slug)
    state.phase = "idle"
    state.export_date = None
    state.cycle_started_at = None
    state.discord_message_id = None
    state.discord_channel_id = None
    state.discord_payload_hash = None
    state.tracker_last_message_id = None
    state.live_exported_fhm_team_ids_json = "[]"
    state.finalize_on_ack = False
    state.updated_at = datetime.utcnow()
    session.flush()
    return state


def start_sim_cycle(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    *,
    export_date: date | None = None,
    cycle_started_at: datetime | None = None,
) -> tuple[SimCycleState, bool]:
    slug = str(league_slug or "").strip()
    exp = export_date or datetime.utcnow().date()
    state = get_or_create_sim_cycle_state(site_session, slug)
    state.phase = "live"
    state.export_date = exp
    state.cycle_started_at = cycle_started_at or datetime.utcnow()
    state.discord_message_id = None
    state.discord_channel_id = None
    state.discord_payload_hash = None
    state.tracker_last_message_id = None
    state.live_exported_fhm_team_ids_json = "[]"
    state.finalize_on_ack = False
    state.updated_at = datetime.utcnow()
    site_session.flush()
    return state, True


def restart_sim_cycle_after_close_ack(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> tuple[SimCycleState, bool]:
    """After closed recap delivery: begin internal live tracking (FTP bot posts #sim-log)."""
    slug = str(league_slug or "").strip()
    state = get_or_create_sim_cycle_state(site_session, slug)
    cycle_anchor = state.cycle_started_at
    reset_sim_cycle_state(site_session, slug)
    return start_sim_cycle(
        site_session,
        league_session,
        slug,
        export_date=datetime.utcnow().date(),
        cycle_started_at=cycle_anchor,
    )


def sim_log_route_ready(site_session: Session, league_slug: str) -> bool:
    """True when #sim-log is configured for sim cycle boards."""
    from app.services.discord_events import (
        SIM_CYCLE_UPDATE_EVENT_KEY,
        is_discord_event_route_active,
    )

    slug = str(league_slug or "").strip()
    if not slug:
        return False
    return bool(
        is_discord_event_route_active(
            site_session, league_slug=slug, event_key=SIM_CYCLE_UPDATE_EVENT_KEY
        )
    )


def sim_cycle_tracker_route_ready(site_session: Session, league_slug: str) -> bool:
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    return bool(_tracker_channel_id(site_session, slug))


def sim_cycle_routes_ready(site_session: Session, league_slug: str) -> bool:
    """#sim-log output plus #gm-export-tracker poll (required for live board updates)."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    return sim_log_route_ready(site_session, slug) and sim_cycle_tracker_route_ready(
        site_session, slug
    )


def publish_closed_sim_cycle_from_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> bool:
    """Queue a new closed #sim-log recap; on delivery ack a fresh live cycle starts."""
    slug = str(league_slug or "").strip()
    if not slug or not sim_log_route_ready(site_session, slug):
        return False
    state = get_or_create_sim_cycle_state(site_session, slug)
    state.phase = "closed"
    state.export_date = export_date
    state.cycle_started_at = datetime.utcnow()
    state.finalize_on_ack = True
    state.updated_at = datetime.utcnow()
    site_session.flush()
    return maybe_enqueue_sim_cycle_discord(
        site_session,
        league_session,
        state,
        force=True,
        post_new_message=True,
        finalize_on_ack=True,
    )


def handle_sim_cycle_after_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> str:
    """Post a closed recap when admin runs EXPORT; live cycle starts after that delivery."""
    if publish_closed_sim_cycle_from_admin_export(
        site_session, league_session, league_slug, export_date
    ):
        return "closed"
    return "none"


def ingest_tracker_messages(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    messages: list[dict[str, Any]],
    *,
    initial_sync: bool = False,
) -> bool:
    """
    Re-scan recent #gm-export-tracker posts and refresh the live export board.

    Uses the full message window each poll (not incremental merge) so exports are
    not lost when an earlier parse pass missed a post.
    """
    from app.services.sim_cycle_tracker_parser import (
        newest_message_id,
        parse_export_fhm_team_ids_from_messages,
        tracker_watermark_before_cycle,
    )

    slug = str(league_slug or "").strip()
    state = site_session.scalar(
        select(SimCycleState).where(
            SimCycleState.league_slug == slug,
            SimCycleState.phase == "live",
        ).limit(1)
    )
    if state is None:
        return False

    allowed: set[str] | None = None
    bot_id = str(state.tracker_bot_user_id or "").strip()
    if bot_id:
        allowed = {bot_id}

    if initial_sync and not str(state.tracker_last_message_id or "").strip():
        watermark = tracker_watermark_before_cycle(
            messages, cycle_started_at=state.cycle_started_at
        )
        if watermark:
            state.tracker_last_message_id = watermark[:32]

    parsed_ids, _latest_mid = parse_export_fhm_team_ids_from_messages(
        slug,
        messages,
        cycle_started_at=state.cycle_started_at,
        allowed_author_ids=allowed,
        require_bot_author=True,
    )
    cursor = newest_message_id(messages)
    if cursor:
        state.tracker_last_message_id = cursor[:32]

    current = _load_live_exported_fhm_ids(state)
    merged = current | parsed_ids
    if merged == current:
        return False
    _store_live_exported_fhm_ids(state, merged)
    state.updated_at = datetime.utcnow()
    site_session.flush()
    return True


def recover_stalled_live_sim_cycle(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> bool:
    """Start live when a prior closed recap was delivered before live auto-start existed."""
    slug = str(league_slug or "").strip()
    if not slug or not sim_log_route_ready(site_session, slug):
        return False
    state = site_session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    if state is None:
        return False
    if str(state.phase or "") != "closed":
        return False
    if bool(state.finalize_on_ack):
        return False
    _state, queued = restart_sim_cycle_after_close_ack(site_session, league_session, slug)
    return queued


def force_start_live_sim_cycle(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> tuple[bool, str]:
    """Admin one-shot: start internal live tracking after a closed recap (no news-bot post)."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False, "Missing league slug."
    if not sim_log_route_ready(site_session, slug):
        return (
            False,
            "Sim log route is not configured. Map sim_cycle_update → #sim-log channel ID.",
        )
    state = site_session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    phase = str(getattr(state, "phase", None) or "idle")
    if state is None or phase != "closed":
        if phase == "live":
            return False, "Sim cycle is already live. FTP bot posts live #sim-log updates."
        return False, "No closed sim cycle to promote. Run EXPORT in the AP ledger first."
    _state, started = restart_sim_cycle_after_close_ack(site_session, league_session, slug)
    if not started:
        return False, "Live sim cycle could not be started."
    return True, "Started live sim cycle tracking. FTP bot posts live #sim-log updates."


def sim_cycle_tracker_config(site_session: Session, league_slug: str) -> dict[str, Any]:
    slug = str(league_slug or "").strip()
    state = site_session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    phase = str(getattr(state, "phase", None) or "idle")
    tracker_channel_id = _tracker_channel_id(site_session, slug)
    return {
        "ok": True,
        "phase": phase,
        "tracker_channel_id": tracker_channel_id,
        "tracker_ready": bool(tracker_channel_id),
        "tracker_last_message_id": str(getattr(state, "tracker_last_message_id", None) or ""),
        "cycle_started_at": (
            state.cycle_started_at.isoformat() if state and state.cycle_started_at else ""
        ),
        "tracker_bot_user_id": str(getattr(state, "tracker_bot_user_id", None) or ""),
    }


def record_sim_cycle_discord_ack(
    session: Session,
    *,
    event_key: str,
    payload: dict,
    discord_message_id: str,
    discord_channel_id: str = "",
    league_session: Session | None = None,
) -> None:
    if str(event_key or "") != SIM_CYCLE_UPDATE_EVENT_KEY:
        return
    mid = str(discord_message_id or "").strip()
    if not mid:
        return
    league_slug = str(payload.get("source_id") or "").strip()
    if not league_slug:
        return
    state = session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == league_slug).limit(1)
    )
    if state is None:
        return
    channel_id = str(discord_channel_id or "").strip()[:32] or None
    finalize = bool(payload.get("finalize_on_ack")) or bool(state.finalize_on_ack)
    if not finalize:
        state.discord_message_id = mid[:32]
        state.discord_channel_id = channel_id
        content_hash = str(payload.get("content_hash") or "").strip()
        if content_hash:
            state.discord_payload_hash = content_hash[:64]
        state.updated_at = datetime.utcnow()
        session.flush()
        return
    league_sess = league_session or session
    # Begin internal live tracking after the closed recap is delivered. FTP bot
    # posts live #sim-log updates; news-bot does not enqueue live boards.
    if sim_log_route_ready(session, league_slug):
        restart_sim_cycle_after_close_ack(session, league_sess, league_slug)
    else:
        state.finalize_on_ack = False
        state.updated_at = datetime.utcnow()
        session.flush()
