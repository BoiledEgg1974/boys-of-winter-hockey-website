"""Detect broken game / season / all-time / team records and enqueue Discord alerts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GameRecordBaseline, Player, RecordLeaderSnapshot, Team
from app.services.all_time_records import (
    GoalieAllTimeRow,
    SkaterAllTimeRow,
    default_goalie_sort_order,
    default_skater_sort_order,
    fetch_goalie_all_time,
    fetch_skater_all_time,
)
from app.services.discord_events import (
    build_league_public_url,
    enqueue_discord_event,
    is_discord_event_route_active,
    team_fields_for_discord,
)
from app.services.game_records import GameRecordBreak, GameRecordHolder
from app.services.league_season_records import build_league_season_record_sections
from app.services.team_records import build_team_record_leaderboards
from app.services.team_season_records import build_team_season_records_bundle

_log = logging.getLogger(__name__)

RECORD_BROKEN_EVENT_KEY = "record_broken"

_SKATER_ALL_TIME_STATS: tuple[tuple[str, str], ...] = (
    ("goals", "Goals"),
    ("assists", "Assists"),
    ("points", "Points"),
    ("plus_minus", "+/-"),
    ("pim", "PIM"),
    ("pp_goals", "PP Goals"),
    ("pp_assists", "PP Assists"),
    ("sh_goals", "SH Goals"),
    ("sh_assists", "SH Assists"),
    ("gwg", "GWG"),
    ("fights", "Fights"),
    ("hits", "Hits"),
    ("gva", "Giveaways"),
    ("tka", "Takeaways"),
    ("sb", "Blocked Shots"),
    ("shots", "Shots"),
    ("gp", "Games Played"),
)

_GOALIE_ALL_TIME_STATS: tuple[tuple[str, str], ...] = (
    ("wins", "Wins"),
    ("losses", "Losses"),
    ("otl", "OTL"),
    ("ga", "Goals Against"),
    ("shots_against", "Shots Against"),
    ("shutouts", "Shutouts"),
    ("sv_pct", "Save %"),
    ("gaa", "GAA"),
    ("gp", "Games Played"),
    ("games_started", "Games Started"),
    ("minutes_played", "Minutes"),
)

_SEGMENT_LABEL = {"rs": "Regular Season", "po": "Playoffs"}
_SCOPE_LABEL = {"all": "All Players", "rookie": "Rookies"}


@dataclass(frozen=True)
class RecordHolderState:
    snapshot_key: str
    record_category: str
    record_title: str
    record_scope: str | None
    value: float
    display_value: str
    display_line: str
    entity_key: str
    higher_is_better: bool
    team_id: int | None = None
    fhm_team_id: str | int | None = None
    team_abbrev: str = ""
    team_name: str = ""
    player_id: int | None = None
    player_name: str = ""
    record_path: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "snapshot_key": self.snapshot_key,
            "record_category": self.record_category,
            "record_title": self.record_title,
            "record_scope": self.record_scope,
            "value": self.value,
            "display_value": self.display_value,
            "display_line": self.display_line,
            "entity_key": self.entity_key,
            "higher_is_better": self.higher_is_better,
            "team_id": self.team_id,
            "fhm_team_id": self.fhm_team_id,
            "team_abbrev": self.team_abbrev,
            "team_name": self.team_name,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "record_path": self.record_path,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> RecordHolderState | None:
        try:
            value = float(data["value"])
        except (KeyError, TypeError, ValueError):
            return None
        if value != value:
            return None
        return cls(
            snapshot_key=str(data.get("snapshot_key") or ""),
            record_category=str(data.get("record_category") or ""),
            record_title=str(data.get("record_title") or ""),
            record_scope=(str(data["record_scope"]) if data.get("record_scope") else None),
            value=value,
            display_value=str(data.get("display_value") or ""),
            display_line=str(data.get("display_line") or ""),
            entity_key=str(data.get("entity_key") or ""),
            higher_is_better=bool(data.get("higher_is_better", True)),
            team_id=_as_int_or_none(data.get("team_id")),
            fhm_team_id=data.get("fhm_team_id"),
            team_abbrev=str(data.get("team_abbrev") or ""),
            team_name=str(data.get("team_name") or ""),
            player_id=_as_int_or_none(data.get("player_id")),
            player_name=str(data.get("player_name") or ""),
            record_path=str(data.get("record_path") or ""),
        )


def _as_int_or_none(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _slug_key(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s or "record"


def _is_better(new_val: float, old_val: float, *, higher_is_better: bool) -> bool:
    if higher_is_better:
        return new_val > old_val
    return new_val < old_val


def _player_display_name(player: Player | None) -> str:
    if player is None:
        return "Unknown"
    name = str(getattr(player, "full_name", None) or "").strip()
    if name:
        return name
    first = str(getattr(player, "first_name", "") or "").strip()
    last = str(getattr(player, "last_name", "") or "").strip()
    return f"{first} {last}".strip() or "Unknown"


def _team_bits(team: Team | None) -> tuple[int | None, str | int | None, str, str]:
    if team is None:
        return None, None, "", ""
    tid = _as_int_or_none(getattr(team, "id", None))
    fhm = getattr(team, "fhm_team_id", None)
    abbr = str(getattr(team, "abbreviation", "") or "").strip()
    name_fn = getattr(team, "full_display_name", None)
    if callable(name_fn):
        name = str(name_fn() or "").strip()
    else:
        name = str(getattr(team, "name", "") or "").strip()
    return tid, fhm, abbr, name


def _format_player_line(
    *,
    player_name: str,
    team_abbrev: str,
    display_value: str,
    season_label: str = "",
    opponent_abbrev: str = "",
    career: bool = False,
) -> str:
    who = player_name or "Unknown"
    if team_abbrev:
        who = f"{who} ({team_abbrev})"
    parts = [f"{who} — {display_value}"]
    if opponent_abbrev:
        parts[0] = f"{who} — {display_value} vs {opponent_abbrev}"
    if season_label:
        parts.append(season_label)
    if career and not season_label:
        parts.append("career")
    return " · ".join(parts)


def format_game_holder_line(holder: GameRecordHolder) -> str:
    player_name = _player_display_name(holder.player)
    _, _, abbr, _ = _team_bits(holder.team)
    _, _, opp_abbr, _ = _team_bits(holder.opponent_team)
    return _format_player_line(
        player_name=player_name,
        team_abbrev=abbr,
        display_value=str(holder.display_value or ""),
        season_label=str(holder.season_label or "").strip(),
        opponent_abbrev=opp_abbr,
    )


def _holder_from_season_row(
    *,
    snapshot_key: str,
    record_title: str,
    record_scope: str,
    row: dict[str, Any],
    team: Team | None,
    record_path: str,
) -> RecordHolderState | None:
    raw = row.get("raw_value")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    player = row.get("player")
    row_team = row.get("team") if row.get("team") is not None else team
    tid, fhm, abbr, tname = _team_bits(row_team)
    player_name = _player_display_name(player if isinstance(player, Player) else None)
    pid = _as_int_or_none(getattr(player, "id", None)) if player is not None else None
    display_value = str(row.get("value") or "")
    season_label = str(row.get("season") or "").strip()
    display_line = _format_player_line(
        player_name=player_name,
        team_abbrev=abbr,
        display_value=display_value,
        season_label=season_label,
    )
    entity = f"player:{pid}" if pid is not None else f"name:{player_name.lower()}"
    return RecordHolderState(
        snapshot_key=snapshot_key,
        record_category="season",
        record_title=record_title,
        record_scope=record_scope,
        value=value,
        display_value=display_value,
        display_line=display_line,
        entity_key=entity,
        higher_is_better=bool(row.get("higher_is_better", True)),
        team_id=tid,
        fhm_team_id=fhm,
        team_abbrev=abbr,
        team_name=tname,
        player_id=pid,
        player_name=player_name,
        record_path=record_path,
    )


def _all_time_stat_value(row: SkaterAllTimeRow | GoalieAllTimeRow, sort_key: str) -> float | None:
    attr_map = {
        "ga": "goals_against",
        "otl": "ties_otl",
        "shots_against": "shots_against",
        "sv_pct": "sv_pct",
        "gaa": "gaa",
        "games_started": "games_started",
        "minutes_played": "minutes_played",
    }
    attr = attr_map.get(sort_key, sort_key)
    raw = getattr(row, attr, None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _format_all_time_display(sort_key: str, value: float) -> str:
    if sort_key == "sv_pct":
        s = f"{value:.3f}"
        return s[1:] if s.startswith("0") else s
    if sort_key == "gaa":
        return f"{value:.2f}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _holder_from_all_time_row(
    *,
    snapshot_key: str,
    record_title: str,
    split: str,
    sort_key: str,
    row: SkaterAllTimeRow | GoalieAllTimeRow,
    higher_is_better: bool,
) -> RecordHolderState | None:
    value = _all_time_stat_value(row, sort_key)
    if value is None:
        return None
    player = row.player
    team = getattr(player, "current_team", None)
    tid, fhm, abbr, tname = _team_bits(team)
    player_name = _player_display_name(player)
    pid = _as_int_or_none(getattr(player, "id", None))
    display_value = _format_all_time_display(sort_key, value)
    span = str(getattr(row, "career_span", None) or "").strip()
    display_line = _format_player_line(
        player_name=player_name,
        team_abbrev=abbr,
        display_value=display_value,
        season_label=span or "career",
        career=not span,
    )
    return RecordHolderState(
        snapshot_key=snapshot_key,
        record_category="all_time",
        record_title=record_title,
        record_scope="league",
        value=value,
        display_value=display_value,
        display_line=display_line,
        entity_key=f"player:{pid}" if pid is not None else f"name:{player_name.lower()}",
        higher_is_better=higher_is_better,
        team_id=tid,
        fhm_team_id=fhm,
        team_abbrev=abbr,
        team_name=tname,
        player_id=pid,
        player_name=player_name,
        record_path=f"/records?split={split}",
    )


def _holder_from_team_row(
    *,
    snapshot_key: str,
    record_title: str,
    row: dict[str, Any],
) -> RecordHolderState | None:
    raw = row.get("raw_value")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    team = row.get("team")
    tid, fhm, abbr, tname = _team_bits(team if isinstance(team, Team) else None)
    if not tname:
        tname = str(row.get("team_name") or "").strip()
    display_value = str(row.get("value") or "")
    year_label = str(row.get("year_label") or "").strip()
    who = tname or abbr or "Unknown"
    display_line = f"{who} — {display_value}"
    if year_label:
        display_line = f"{display_line} · {year_label}"
    entity = f"team:{tid}" if tid is not None else f"name:{(tname or abbr).lower()}"
    if year_label:
        entity = f"{entity}:{year_label}"
    return RecordHolderState(
        snapshot_key=snapshot_key,
        record_category="team",
        record_title=record_title,
        record_scope="league",
        value=value,
        display_value=display_value,
        display_line=display_line,
        entity_key=entity,
        higher_is_better=bool(row.get("higher_is_better", True)),
        team_id=tid,
        fhm_team_id=fhm,
        team_abbrev=abbr,
        team_name=tname,
        record_path="/team-records",
    )


def collect_current_record_holders(session: Session, *, league_slug: str) -> dict[str, RecordHolderState]:
    """Build current #1 holders for season / all-time / team boards."""
    out: dict[str, RecordHolderState] = {}

    for segment in ("rs", "po"):
        seg_label = _SEGMENT_LABEL.get(segment, segment)
        for section in build_league_season_record_sections(session, segment):
            if not section.rows:
                continue
            key = f"season:league:{segment}:{_slug_key(section.title)}"
            title = f"League Season Record — {section.title} ({seg_label})"
            holder = _holder_from_season_row(
                snapshot_key=key,
                record_title=title,
                record_scope="league",
                row=section.rows[0],
                team=None,
                record_path=f"/season-records?segment={segment}",
            )
            if holder is not None:
                out[key] = holder

    teams = list(session.scalars(select(Team).order_by(Team.id.asc())).all())
    for team in teams:
        rs_sections, po_sections = build_team_season_records_bundle(session, team)
        for segment, sections in (("rs", rs_sections), ("po", po_sections)):
            seg_label = _SEGMENT_LABEL.get(segment, segment)
            for section in sections:
                if not section.rows:
                    continue
                key = f"season:franchise:{int(team.id)}:{segment}:{_slug_key(section.title)}"
                title = (
                    f"Franchise Season Record — {section.title} ({seg_label}) "
                    f"· {team.abbreviation or team.name}"
                )
                holder = _holder_from_season_row(
                    snapshot_key=key,
                    record_title=title,
                    record_scope="franchise",
                    row=section.rows[0],
                    team=team,
                    record_path=f"/team/{team.slug}?panel=season_records",
                )
                if holder is not None:
                    out[key] = holder

    for split in ("rs", "po"):
        seg_label = _SEGMENT_LABEL.get(split, split)
        for sort_key, label in _SKATER_ALL_TIME_STATS:
            rows, _, _ = fetch_skater_all_time(
                session,
                split,  # type: ignore[arg-type]
                sort_key,
                default_skater_sort_order(sort_key),
                "all",
            )
            if not rows:
                continue
            key = f"all_time:{split}:skater:{sort_key}"
            title = f"All-Time Record — {label} ({seg_label})"
            higher = default_skater_sort_order(sort_key) != "asc"
            holder = _holder_from_all_time_row(
                snapshot_key=key,
                record_title=title,
                split=split,
                sort_key=sort_key,
                row=rows[0],
                higher_is_better=higher,
            )
            if holder is not None:
                out[key] = holder

        for sort_key, label in _GOALIE_ALL_TIME_STATS:
            rows, _, _ = fetch_goalie_all_time(
                session,
                split,  # type: ignore[arg-type]
                sort_key,
                default_goalie_sort_order(sort_key),
                "all",
            )
            if not rows:
                continue
            key = f"all_time:{split}:goalie:{sort_key}"
            title = f"All-Time Record — Goalie {label} ({seg_label})"
            higher = default_goalie_sort_order(sort_key) != "asc"
            holder = _holder_from_all_time_row(
                snapshot_key=key,
                record_title=title,
                split=split,
                sort_key=sort_key,
                row=rows[0],
                higher_is_better=higher,
            )
            if holder is not None:
                out[key] = holder

    for section in build_team_record_leaderboards(session, league_slug=league_slug):
        if not section.rows:
            continue
        key = f"team:{_slug_key(section.title)}"
        title = f"Team Record — {section.title}"
        holder = _holder_from_team_row(
            snapshot_key=key,
            record_title=title,
            row=section.rows[0],
        )
        if holder is not None:
            out[key] = holder

    return out


def _load_snapshot_map(session: Session) -> dict[str, RecordHolderState]:
    out: dict[str, RecordHolderState] = {}
    rows = session.scalars(select(RecordLeaderSnapshot)).all()
    for row in rows:
        try:
            data = json.loads(row.holder_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        holder = RecordHolderState.from_json_dict(data)
        if holder is None:
            continue
        key = str(row.snapshot_key or holder.snapshot_key or "").strip()
        if key:
            out[key] = holder
    return out


def _upsert_snapshot(session: Session, holder: RecordHolderState) -> None:
    row = session.scalar(
        select(RecordLeaderSnapshot)
        .where(RecordLeaderSnapshot.snapshot_key == holder.snapshot_key)
        .limit(1)
    )
    payload = json.dumps(holder.to_json_dict(), separators=(",", ":"), sort_keys=True)
    now = datetime.utcnow()
    if row is None:
        session.add(
            RecordLeaderSnapshot(
                snapshot_key=holder.snapshot_key,
                holder_json=payload,
                updated_at=now,
            )
        )
    else:
        row.holder_json = payload
        row.updated_at = now


def detect_snapshot_breaks(
    previous: dict[str, RecordHolderState],
    current: dict[str, RecordHolderState],
) -> list[tuple[RecordHolderState | None, RecordHolderState]]:
    """Return (old, new) pairs where new strictly beats a previously seeded holder."""
    breaks: list[tuple[RecordHolderState | None, RecordHolderState]] = []
    for key, new_holder in current.items():
        old = previous.get(key)
        if old is None:
            continue
        if not _is_better(
            new_holder.value,
            old.value,
            higher_is_better=new_holder.higher_is_better,
        ):
            continue
        breaks.append((old, new_holder))
    return breaks


def _game_break_title(br: GameRecordBreak) -> str:
    seg = _SEGMENT_LABEL.get(br.segment, br.segment)
    scope = _SCOPE_LABEL.get(br.scope, br.scope)
    return f"Game Record — {br.metric.title} ({seg}, {scope})"


def _payload_from_holders(
    *,
    league_slug: str,
    old: RecordHolderState | None,
    new: RecordHolderState,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": new.record_title,
        "record_category": new.record_category,
        "record_scope": new.record_scope or "",
        "record_title": new.record_title,
        "old_record_line": (old.display_line if old is not None else "—"),
        "new_record_line": new.display_line,
        "player_id": new.player_id,
        "player_name": new.player_name,
        "record_path": new.record_path or "",
    }
    if new.team_id is not None:
        payload["team_id"] = new.team_id
    if new.fhm_team_id is not None and str(new.fhm_team_id).strip():
        payload["fhm_team_id"] = new.fhm_team_id
    if new.team_abbrev:
        payload["team_abbrev"] = new.team_abbrev
    if new.team_name:
        payload["team_name"] = new.team_name
    if new.record_path:
        url = build_league_public_url(league_slug, new.record_path)
        if url:
            payload["record_url"] = url
            payload["url"] = url
    return payload


def _payload_from_game_break(*, league_slug: str, br: GameRecordBreak) -> dict[str, Any]:
    new = br.new_holder
    old = br.old_holder
    title = _game_break_title(br)
    record_path = (
        f"/game-records?segment={br.segment}&scope={br.scope}&player_kind={br.metric.player_kind}"
    )
    payload: dict[str, Any] = {
        "title": title,
        "record_category": "game",
        "record_scope": "league",
        "record_title": title,
        "old_record_line": format_game_holder_line(old) if old is not None else "—",
        "new_record_line": format_game_holder_line(new),
        "player_id": _as_int_or_none(getattr(new.player, "id", None)),
        "player_name": _player_display_name(new.player),
        "record_path": record_path,
    }
    if new.team is not None:
        payload.update(team_fields_for_discord(new.team))
    url = build_league_public_url(league_slug, record_path)
    if url:
        payload["record_url"] = url
        payload["url"] = url
    return payload


def _source_id_for_holder(holder: RecordHolderState) -> str:
    return f"{holder.snapshot_key}:{holder.entity_key}:{holder.value}"


def _source_id_for_game_break(br: GameRecordBreak) -> str:
    pid = _as_int_or_none(getattr(br.new_holder.player, "id", None)) or 0
    val = br.new_holder.value
    return f"{br.snapshot_key}:player:{pid}:{val}"


def _refresh_record_payload_urls(league_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Rebuild absolute record URLs using the current SITE_PUBLIC_BASE_URL."""
    out = dict(payload)
    path = str(out.get("record_path") or "").strip()
    if not path:
        return out
    url = build_league_public_url(league_slug, path)
    if url:
        out["record_url"] = url
        out["url"] = url
    return out


def enqueue_record_broken_event(
    site_session: Session,
    *,
    league_slug: str,
    payload: dict[str, Any],
    source_id: str,
) -> bool:
    if not is_discord_event_route_active(
        site_session, league_slug=league_slug, event_key=RECORD_BROKEN_EVENT_KEY
    ):
        return False
    row = enqueue_discord_event(
        site_session,
        league_slug=league_slug,
        event_key=RECORD_BROKEN_EVENT_KEY,
        payload=_refresh_record_payload_urls(league_slug, payload),
        created_by_user_id=None,
        source_type="record_broken",
        source_id=source_id,
    )
    return row is not None


def enqueue_record_broken_events_from_deploy(
    site_session: Session,
    *,
    league_slug: str,
    events: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Enqueue sidecar / reconstructed record-break events against live Discord routes."""
    stats = {"events": 0, "queued": 0}
    for raw in events or []:
        source_id = str(raw.get("source_id") or "").strip()
        payload = raw.get("payload")
        if not source_id or not isinstance(payload, dict):
            continue
        stats["events"] += 1
        if enqueue_record_broken_event(
            site_session,
            league_slug=league_slug,
            payload=payload,
            source_id=source_id,
        ):
            stats["queued"] += 1
    return stats


def collect_live_record_state(league_session: Session) -> dict[str, Any]:
    """Serialize current record snapshots + game baselines for deploy-db fallback."""
    from app.services.game_records import (
        format_game_record_value,
        game_record_metrics,
        game_record_snapshot_key,
    )

    snapshots = {
        key: holder.to_json_dict()
        for key, holder in _load_snapshot_map(league_session).items()
    }
    metrics = {
        (metric.player_kind, metric.key): metric
        for player_kind in ("skater", "goalie")
        for metric in game_record_metrics(player_kind=player_kind)
    }
    game_baselines: dict[str, dict[str, Any]] = {}
    rows = list(league_session.scalars(select(GameRecordBaseline)).all())
    for row in rows:
        metric = metrics.get((str(row.player_kind or ""), str(row.metric_key or "")))
        if metric is None:
            continue
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            continue
        key = game_record_snapshot_key(
            segment=str(row.segment or "rs"),
            scope=str(row.scope or "all"),
            player_kind=metric.player_kind,
            metric_key=metric.key,
        )
        _, _, team_abbrev, _ = _team_bits(row.team)
        _, _, opp_abbrev, _ = _team_bits(row.opponent_team)
        display_value = format_game_record_value(value, metric)
        game_baselines[key] = {
            "snapshot_key": key,
            "metric_key": metric.key,
            "metric_title": metric.title,
            "player_kind": metric.player_kind,
            "segment": str(row.segment or "rs"),
            "scope": str(row.scope or "all"),
            "value": value,
            "display_value": display_value,
            "player_name": _player_display_name(row.player),
            "team_abbrev": team_abbrev,
            "opponent_abbrev": opp_abbrev,
            "season_label": str(row.season_label or "").strip(),
            "higher_is_better": bool(metric.higher_is_better),
        }
    return {"snapshots": snapshots, "game_baselines": game_baselines}


def events_from_live_record_state_diff(
    league_session: Session,
    *,
    league_slug: str,
    live_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build record-break events by comparing pre-promote live state to the new DB."""
    if not live_state:
        return []
    events: list[dict[str, Any]] = []
    previous: dict[str, RecordHolderState] = {}
    for key, raw in (live_state.get("snapshots") or {}).items():
        if not isinstance(raw, dict):
            continue
        holder = RecordHolderState.from_json_dict(raw)
        if holder is None:
            continue
        previous[str(key)] = holder
    if previous:
        current = collect_current_record_holders(league_session, league_slug=league_slug)
        for old, new in detect_snapshot_breaks(previous, current):
            events.append(
                {
                    "source_id": _source_id_for_holder(new),
                    "payload": _payload_from_holders(league_slug=league_slug, old=old, new=new),
                }
            )

    live_games = live_state.get("game_baselines") or {}
    if isinstance(live_games, dict) and live_games:
        fresh = collect_live_record_state(league_session).get("game_baselines") or {}
        for key, new_raw in fresh.items():
            old_raw = live_games.get(key)
            if not isinstance(old_raw, dict) or not isinstance(new_raw, dict):
                continue
            try:
                old_val = float(old_raw["value"])
                new_val = float(new_raw["value"])
            except (KeyError, TypeError, ValueError):
                continue
            higher = bool(new_raw.get("higher_is_better", True))
            if not _is_better(new_val, old_val, higher_is_better=higher):
                continue
            segment = str(new_raw.get("segment") or "rs")
            scope = str(new_raw.get("scope") or "all")
            player_kind = str(new_raw.get("player_kind") or "skater")
            title = (
                f"Game Record — {new_raw.get('metric_title') or new_raw.get('metric_key')} "
                f"({_SEGMENT_LABEL.get(segment, segment)}, {_SCOPE_LABEL.get(scope, scope)})"
            )
            record_path = (
                f"/game-records?segment={segment}&scope={scope}&player_kind={player_kind}"
            )
            old_line = _format_player_line(
                player_name=str(old_raw.get("player_name") or "Unknown"),
                team_abbrev=str(old_raw.get("team_abbrev") or ""),
                display_value=str(old_raw.get("display_value") or old_val),
                season_label=str(old_raw.get("season_label") or ""),
                opponent_abbrev=str(old_raw.get("opponent_abbrev") or ""),
            )
            new_line = _format_player_line(
                player_name=str(new_raw.get("player_name") or "Unknown"),
                team_abbrev=str(new_raw.get("team_abbrev") or ""),
                display_value=str(new_raw.get("display_value") or new_val),
                season_label=str(new_raw.get("season_label") or ""),
                opponent_abbrev=str(new_raw.get("opponent_abbrev") or ""),
            )
            events.append(
                {
                    "source_id": f"{key}:player:0:{new_val}",
                    "payload": {
                        "title": title,
                        "record_category": "game",
                        "record_scope": "league",
                        "record_title": title,
                        "old_record_line": old_line,
                        "new_record_line": new_line,
                        "player_name": str(new_raw.get("player_name") or ""),
                        "record_path": record_path,
                    },
                }
            )
    return events


def notify_record_breaks_after_import(
    league_session: Session,
    site_session: Session,
    *,
    league_slug: str,
    game_breaks: list[GameRecordBreak] | None = None,
    notify: bool = True,
) -> dict[str, int]:
    """Diff record boards after import; seed snapshots on first run; enqueue Discord breaks.

    ``notify=False`` still updates snapshots / processes game promotions but skips Discord
    (used on app boot so startup baseline sync does not flood channels).
    """
    slug = str(league_slug or "").strip()
    stats = {"seeded": 0, "updated": 0, "queued": 0, "game_breaks": 0, "sidecar": 0}
    pending_events: list[dict[str, Any]] = []

    if notify and game_breaks:
        for br in game_breaks:
            payload = _payload_from_game_break(league_slug=slug, br=br)
            source_id = _source_id_for_game_break(br)
            pending_events.append({"source_id": source_id, "payload": payload})
            if enqueue_record_broken_event(
                site_session,
                league_slug=slug,
                payload=payload,
                source_id=source_id,
            ):
                stats["queued"] += 1
                stats["game_breaks"] += 1

    try:
        previous = _load_snapshot_map(league_session)
        current = collect_current_record_holders(league_session, league_slug=slug)
    except Exception:
        _log.exception("record holder snapshot collect failed for %s", slug)
        _record_deploy_sidecar(slug, pending_events, stats)
        return stats

    if not previous:
        for holder in current.values():
            _upsert_snapshot(league_session, holder)
            stats["seeded"] += 1
        _record_deploy_sidecar(slug, pending_events, stats)
        return stats

    if notify:
        for old, new in detect_snapshot_breaks(previous, current):
            payload = _payload_from_holders(league_slug=slug, old=old, new=new)
            source_id = _source_id_for_holder(new)
            pending_events.append({"source_id": source_id, "payload": payload})
            if enqueue_record_broken_event(
                site_session,
                league_slug=slug,
                payload=payload,
                source_id=source_id,
            ):
                stats["queued"] += 1

    for holder in current.values():
        _upsert_snapshot(league_session, holder)
        stats["updated"] += 1

    _record_deploy_sidecar(slug, pending_events, stats)
    return stats


def _record_deploy_sidecar(
    league_slug: str,
    events: list[dict[str, Any]],
    stats: dict[str, int],
) -> None:
    if not league_slug or not events:
        return
    try:
        from app.services.deploy_discord_records import record_deploy_record_break_events

        stats["sidecar"] = record_deploy_record_break_events(league_slug, events)
    except Exception:
        _log.exception(
            "Deploy Discord records sidecar write failed for %s (non-fatal)",
            league_slug,
        )
