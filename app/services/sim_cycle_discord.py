"""Discord sim cycle export board (#sim-log closed recap after admin EXPORT)."""
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
from app.services.staff_salaries import main_league_teams
from app.services.discord_events import (
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


def _division_maps_for_league(league_slug: str) -> tuple[dict[tuple[int, int], str], dict[int, str]]:
    raw_name = league_raw_import_dir(league_slug)
    div_csv = Path("data/imports/raw") / raw_name / "divisions.csv"
    return load_division_display_maps(div_csv)


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


def build_division_export_groups(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    exported_fhm_team_ids: set[int],
) -> list[dict[str, Any]]:
    div_by_pair, div_by_id = _division_maps_for_league(league_slug)
    memberships_by_team_id = _memberships_by_team_id(site_session, league_slug)
    groups: dict[str, dict[str, list[int]]] = {}

    for team in _sim_cycle_teams_for_league(league_session):
        mem = memberships_by_team_id.get(int(team.id))
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
) -> dict[str, Any]:
    league_slug = str(state.league_slug or "")
    export_date = state.export_date
    now = datetime.utcnow()
    from scripts.league_discord_bot.team_maps import sim_cycle_embed_color

    exported_ids: set[int] = set()
    if export_date is not None:
        exported_ids = _closed_exported_fhm_team_ids(
            site_session, league_session, league_slug, export_date
        )

    divisions = build_division_export_groups(
        site_session, league_session, league_slug, exported_ids
    )
    total_teams = sum(len(d["exported"]) + len(d["pending"]) for d in divisions)
    exported_count = len(exported_ids)

    payload: dict[str, Any] = {
        "title": "Current Sim Cycle (closed)",
        "phase": "closed",
        "export_date": export_date.isoformat() if export_date else "",
        "divisions": divisions,
        "exported_fhm_team_ids": sorted(exported_ids),
        "total_teams": total_teams,
        "exported_count": exported_count,
        "last_updated_at": now.isoformat(),
        "embed_color": sim_cycle_embed_color(league_slug),
        "source_type": "sim_cycle_state",
        "source_id": league_slug,
        "content_hash": "",
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
) -> bool:
    if str(state.phase or "") != "closed":
        return False
    payload = build_sim_cycle_discord_payload(
        site_session,
        league_session,
        state,
        post_new_message=post_new_message,
    )
    content_hash = str(payload.get("content_hash") or "")
    prev_hash = str(state.discord_payload_hash or "").strip()
    if not force and content_hash and content_hash == prev_hash:
        return False
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


def sim_log_route_ready(site_session: Session, league_slug: str) -> bool:
    """True when #sim-log is configured for closed export recaps."""
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


def publish_closed_sim_cycle_from_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> bool:
    """Queue a closed #sim-log board from GM export attendance for *export_date*."""
    slug = str(league_slug or "").strip()
    if not slug or not sim_log_route_ready(site_session, slug):
        return False
    state = get_or_create_sim_cycle_state(site_session, slug)
    state.phase = "closed"
    state.export_date = export_date
    state.finalize_on_ack = False
    state.updated_at = datetime.utcnow()
    site_session.flush()
    post_new = not resolve_sim_cycle_discord_message_id(site_session, slug)
    return maybe_enqueue_sim_cycle_discord(
        site_session,
        league_session,
        state,
        force=True,
        post_new_message=post_new,
    )


def handle_sim_cycle_after_admin_export(
    site_session: Session,
    league_session: Session,
    league_slug: str,
    export_date: date,
) -> str:
    """Post or update the closed sim cycle board when admin runs EXPORT."""
    if publish_closed_sim_cycle_from_admin_export(
        site_session, league_session, league_slug, export_date
    ):
        return "closed"
    return "none"


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
    state.discord_message_id = mid[:32]
    state.discord_channel_id = channel_id
    content_hash = str(payload.get("content_hash") or "").strip()
    if content_hash:
        state.discord_payload_hash = content_hash[:64]
    state.updated_at = datetime.utcnow()
    session.flush()
    _ = league_session
