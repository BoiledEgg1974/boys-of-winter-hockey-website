"""Discord sim cycle export board (#sim-log live + closed)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import league_raw_import_dir
from app.models import Team
from app.services.division_labels import load_division_display_maps, team_division_display_label
from app.services.discord_events import (
    GM_EXPORT_TRACKER_POLL_EVENT_KEY,
    SIM_CYCLE_UPDATE_EVENT_KEY,
    bot_event_delivery_fields,
    enqueue_repeatable_discord_event,
)
from app.site_models import GmExportAttendance, GmLeagueMembership, SimCycleState

_SIM_CYCLE_EMBED_COLOR = 0xF1C40F


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


def _division_maps_for_league(league_slug: str) -> tuple[dict[tuple[int, int], str], dict[int, str]]:
    raw_name = league_raw_import_dir(league_slug)
    div_csv = Path("data/imports/raw") / raw_name / "divisions.csv"
    return load_division_display_maps(div_csv)


def _active_teams_for_league(
    site_session: Session, league_session: Session, league_slug: str
) -> list[tuple[Team, GmLeagueMembership]]:
    memberships = list(
        site_session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    if not memberships:
        return []
    team_ids = sorted({int(m.team_id) for m in memberships})
    teams_by_id = {
        int(t.id): t
        for t in league_session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    }
    out: list[tuple[Team, GmLeagueMembership]] = []
    for mem in memberships:
        team = teams_by_id.get(int(mem.team_id))
        if team is None:
            continue
        out.append((team, mem))
    return out


def _fhm_team_id_for_team(
    team: Team, membership: GmLeagueMembership, *, league_slug: str
) -> int | None:
    """Resolve FHM franchise TeamId (not site teams.id)."""
    from scripts.league_discord_bot.team_maps import teams_for_league_slug

    roster = teams_for_league_slug(league_slug)
    for raw in (team.fhm_team_id, membership.fhm_team_id):
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


def build_division_export_groups(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    exported_fhm_team_ids: set[int],
) -> list[dict[str, Any]]:
    div_by_pair, div_by_id = _division_maps_for_league(league_slug)
    groups: dict[str, dict[str, list[int]]] = {}

    for team, mem in _active_teams_for_league(site_session, league_session, league_slug):
        fhm_id = _fhm_team_id_for_team(team, mem, league_slug=league_slug)
        if fhm_id is None:
            continue
        div_label = team_division_display_label(
            SimpleNamespace(division=""), team, div_by_pair, div_by_id
        )
        div_name = (div_label or "League").strip() or "League"
        bucket = groups.setdefault(div_name, {"exported": [], "pending": []})
        if int(fhm_id) in exported_fhm_team_ids:
            bucket["exported"].append(int(fhm_id))
        else:
            bucket["pending"].append(int(fhm_id))

    divisions: list[dict[str, Any]] = []
    for name in sorted(groups.keys(), key=lambda s: s.lower()):
        bucket = groups[name]
        divisions.append(
            {
                "name": name,
                "exported": sorted(
                    bucket["exported"],
                    key=lambda tid: _abbrev_for_fhm_id(league_slug, int(tid)),
                ),
                "pending": sorted(
                    bucket["pending"],
                    key=lambda tid: _abbrev_for_fhm_id(league_slug, int(tid)),
                ),
            }
        )
    return divisions


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
    memberships = {
        int(m.team_id): m
        for m in site_session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == league_slug,
                GmLeagueMembership.team_id.in_(team_ids),
            )
        ).all()
    }
    exported: set[int] = set()
    for tid in team_ids:
        team = teams.get(tid)
        mem = memberships.get(tid)
        if team is None or mem is None:
            continue
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
    delivery = bot_event_delivery_fields(
        session,
        league_slug=league_slug,
        event_key=GM_EXPORT_TRACKER_POLL_EVENT_KEY,
    )
    return str(delivery.get("discord_channel_id") or "").strip()


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
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
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

    if phase == "closed" and export_date is not None:
        exported_ids = _closed_exported_fhm_team_ids(
            site_session, league_session, league_slug, export_date
        )
    else:
        exported_ids = _load_live_exported_fhm_ids(state)

    divisions = build_division_export_groups(
        site_session, league_session, league_slug, exported_ids
    )
    total_teams = sum(len(d["exported"]) + len(d["pending"]) for d in divisions)
    exported_count = len(exported_ids)

    phase_label = "live" if phase == "live" else "closed" if phase == "closed" else "idle"
    payload: dict[str, Any] = {
        "title": f"Current Sim Cycle ({phase_label})",
        "phase": phase,
        "export_date": export_date.isoformat() if export_date else "",
        "divisions": divisions,
        "exported_fhm_team_ids": sorted(exported_ids),
        "total_teams": total_teams,
        "exported_count": exported_count,
        "last_updated_at": now.isoformat(),
        "embed_color": _SIM_CYCLE_EMBED_COLOR,
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
    if str(state.phase or "") not in {"live", "closed"}:
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
    created_by_user_id: int | None = None,
) -> tuple[SimCycleState, bool]:
    slug = str(league_slug or "").strip()
    exp = export_date or datetime.utcnow().date()
    state = get_or_create_sim_cycle_state(site_session, slug)
    state.phase = "live"
    state.export_date = exp
    state.cycle_started_at = datetime.utcnow()
    state.discord_message_id = None
    state.discord_channel_id = None
    state.discord_payload_hash = None
    state.tracker_last_message_id = None
    state.live_exported_fhm_team_ids_json = "[]"
    state.finalize_on_ack = False
    state.updated_at = datetime.utcnow()
    site_session.flush()
    queued = maybe_enqueue_sim_cycle_discord(
        site_session,
        league_session,
        state,
        force=True,
        post_new_message=True,
    )
    _ = created_by_user_id
    return state, queued


def close_sim_cycle_from_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> bool:
    slug = str(league_slug or "").strip()
    state = site_session.scalar(
        select(SimCycleState).where(
            SimCycleState.league_slug == slug,
            SimCycleState.phase == "live",
        ).limit(1)
    )
    if state is None:
        return False
    if state.export_date is not None and state.export_date != export_date:
        state.export_date = export_date
    state.phase = "closed"
    state.updated_at = datetime.utcnow()
    site_session.flush()
    return maybe_enqueue_sim_cycle_discord(
        site_session,
        league_session,
        state,
        force=True,
        finalize_on_ack=True,
    )


def sim_log_route_ready(site_session: Session, league_slug: str) -> bool:
    """True when #sim-log is configured (required for the board; works in-season and offseason)."""
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
    from app.services.discord_events import (
        GM_EXPORT_TRACKER_POLL_EVENT_KEY,
        is_discord_event_route_active,
    )

    slug = str(league_slug or "").strip()
    if not slug:
        return False
    return bool(
        is_discord_event_route_active(
            site_session, league_slug=slug, event_key=GM_EXPORT_TRACKER_POLL_EVENT_KEY
        )
    )


def sim_cycle_routes_ready(site_session: Session, league_slug: str) -> bool:
    """Sim-log output route; tracker is optional (live board runs without it)."""
    return sim_log_route_ready(site_session, league_slug)


def maybe_auto_start_sim_cycle(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> bool:
    """Start a live cycle when idle and #sim-log is configured (year-round, including offseason)."""
    slug = str(league_slug or "").strip()
    if not slug or not sim_cycle_routes_ready(site_session, slug):
        return False
    state = get_or_create_sim_cycle_state(site_session, slug)
    if str(state.phase or "idle") != "idle":
        return False
    _state, queued = start_sim_cycle(site_session, league_session, slug)
    return queued


def handle_sim_cycle_after_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> str:
    """
    Advance the sim cycle when admin runs EXPORT on the website.

    - live → close (closed PATCH; ack auto-starts next live cycle)
    - idle → start live cycle immediately (bootstrap / between seasons)
    - closed → no-op (close delivery already in flight)
    """
    slug = str(league_slug or "").strip()
    if not slug or not sim_cycle_routes_ready(site_session, slug):
        return "none"
    state = get_or_create_sim_cycle_state(site_session, slug)
    phase = str(state.phase or "idle")
    if phase == "closed":
        return "none"
    if phase == "live":
        return "closed" if close_sim_cycle_from_admin_export(
            site_session, league_session, slug, export_date
        ) else "none"
    if phase == "idle":
        _state, queued = start_sim_cycle(
            site_session,
            league_session,
            slug,
            export_date=datetime.utcnow().date(),
        )
        return "started" if queued else "none"
    return "none"


def ingest_tracker_messages(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    messages: list[dict[str, Any]],
) -> bool:
    from app.services.sim_cycle_tracker_parser import parse_export_fhm_team_ids_from_messages

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

    parsed_ids, latest_mid = parse_export_fhm_team_ids_from_messages(
        slug,
        messages,
        cycle_started_at=state.cycle_started_at,
        allowed_author_ids=allowed,
        require_bot_author=True,
    )
    if latest_mid:
        prev = str(state.tracker_last_message_id or "").strip()
        if not prev or int(latest_mid) > int(prev):
            state.tracker_last_message_id = latest_mid[:32]

    current = _load_live_exported_fhm_ids(state)
    merged = current | parsed_ids
    if merged == current:
        return False
    _store_live_exported_fhm_ids(state, merged)
    state.updated_at = datetime.utcnow()
    site_session.flush()
    return maybe_enqueue_sim_cycle_discord(site_session, league_session, state)


def sim_cycle_tracker_config(site_session: Session, league_slug: str) -> dict[str, Any]:
    slug = str(league_slug or "").strip()
    state = site_session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    phase = str(getattr(state, "phase", None) or "idle")
    return {
        "ok": True,
        "phase": phase,
        "tracker_channel_id": _tracker_channel_id(site_session, slug),
        "tracker_last_message_id": str(getattr(state, "tracker_last_message_id", None) or ""),
        "cycle_started_at": (
            state.cycle_started_at.isoformat() if state and state.cycle_started_at else ""
        ),
        "tracker_bot_user_id": str(getattr(state, "tracker_bot_user_id", None) or ""),
    }


def refresh_sim_cycle_for_discord_poll(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> bool:
    """Keep a live board running whenever #sim-log is configured (including offseason)."""
    return maybe_auto_start_sim_cycle(site_session, league_session, league_slug)


def restart_sim_cycle_after_close_ack(
    site_session: Session,
    league_session: Session,
    league_slug: str,
) -> tuple[SimCycleState, bool]:
    """After closed PATCH delivery: clear prior cycle and POST a fresh live embed."""
    slug = str(league_slug or "").strip()
    reset_sim_cycle_state(site_session, slug)
    return start_sim_cycle(
        site_session,
        league_session,
        slug,
        export_date=datetime.utcnow().date(),
    )


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
    restart_sim_cycle_after_close_ack(session, league_sess, league_slug)
