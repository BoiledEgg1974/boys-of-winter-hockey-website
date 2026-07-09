"""Admin adjustments that exclude or correct rows used on records leaderboards."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import RecordStatAdjustment

ADJ_EXCLUDE = "exclude"
ADJ_OVERRIDE = "override"
LINE_SKATER = "skater_career"
LINE_GOALIE = "goalie_career"

CareerLineKey = tuple[int, int, str | None, str]


def _norm_fhm(raw: Any) -> str | None:
    s = str(raw or "").strip()
    return s or None


def career_line_key(
    *,
    player_id: int,
    season_year: int,
    team_fhm_id: Any = None,
    career_source: str | None = None,
    line_kind: str = LINE_SKATER,
) -> CareerLineKey:
    src = (career_source or "").strip() or "*"
    return (int(player_id), int(season_year), _norm_fhm(team_fhm_id), src)


def _key_from_line(ln: Any, line_kind: str) -> CareerLineKey:
    return career_line_key(
        player_id=int(ln.player_id),
        season_year=int(ln.season_year),
        team_fhm_id=getattr(ln, "team_fhm_id", None),
        career_source=getattr(ln, "career_source", None),
        line_kind=line_kind,
    )


def _line_matches_key(key: CareerLineKey, ln: Any) -> bool:
    pid, sy, fhm, src = key
    if int(ln.player_id) != pid or int(ln.season_year) != sy:
        return False
    ln_fhm = _norm_fhm(getattr(ln, "team_fhm_id", None))
    if fhm and ln_fhm != fhm:
        return False
    ln_src = (getattr(ln, "career_source", None) or "").strip() or "*"
    if src != "*" and ln_src != src:
        return False
    return True


def list_adjustments(session: Session, *, limit: int = 500) -> list[RecordStatAdjustment]:
    from sqlalchemy.orm import joinedload

    return list(
        session.scalars(
            select(RecordStatAdjustment)
            .options(joinedload(RecordStatAdjustment.player))
            .order_by(RecordStatAdjustment.updated_at.desc(), RecordStatAdjustment.id.desc())
            .limit(limit)
        ).all()
    )


def delete_adjustment(session: Session, adjustment_id: int) -> bool:
    row = session.get(RecordStatAdjustment, adjustment_id)
    if row is None:
        return False
    session.delete(row)
    return True


def upsert_career_adjustment(
    session: Session,
    *,
    adjustment_id: int | None,
    adj_type: str,
    line_kind: str,
    player_id: int,
    season_year: int,
    team_fhm_id: str | None,
    career_source: str | None,
    overrides: dict[str, Any] | None,
    notes: str | None,
    user_id: int | None,
) -> RecordStatAdjustment:
    if adj_type not in (ADJ_EXCLUDE, ADJ_OVERRIDE):
        raise ValueError("Adjustment type must be exclude or override.")
    if line_kind not in (LINE_SKATER, LINE_GOALIE):
        raise ValueError("Line kind must be skater_career or goalie_career.")
    if player_id <= 0 or season_year <= 0:
        raise ValueError("Player and season year are required.")

    row = session.get(RecordStatAdjustment, adjustment_id) if adjustment_id else None
    if row is None:
        row = RecordStatAdjustment(adj_type=adj_type, line_kind=line_kind)
    row.adj_type = adj_type
    row.line_kind = line_kind
    row.player_id = int(player_id)
    row.season_year = int(season_year)
    row.team_fhm_id = _norm_fhm(team_fhm_id)
    row.career_source = (career_source or "").strip() or None
    if adj_type == ADJ_OVERRIDE:
        if not overrides:
            raise ValueError("Override adjustments require at least one stat field.")
        row.overrides_json = json.dumps(overrides)
    else:
        row.overrides_json = None
    row.notes = (notes or "").strip() or None
    row.updated_at = datetime.utcnow()
    row.updated_by_user_id = user_id
    session.add(row)
    session.flush()
    return row


def _load_adjustment_maps(session: Session) -> tuple[set[CareerLineKey], dict[CareerLineKey, dict[str, Any]]]:
    excluded: set[CareerLineKey] = set()
    overrides: dict[CareerLineKey, dict[str, Any]] = {}
    for row in session.scalars(select(RecordStatAdjustment)).all():
        if row.player_id is None or row.season_year is None:
            continue
        key = career_line_key(
            player_id=int(row.player_id),
            season_year=int(row.season_year),
            team_fhm_id=row.team_fhm_id,
            career_source=row.career_source,
            line_kind=str(row.line_kind or LINE_SKATER),
        )
        if row.adj_type == ADJ_EXCLUDE:
            excluded.add(key)
            continue
        if row.adj_type == ADJ_OVERRIDE and row.overrides_json:
            try:
                payload = json.loads(row.overrides_json)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload:
                overrides[key] = payload
    return excluded, overrides


_ADJ_CACHE: dict[int, tuple[set[CareerLineKey], dict[CareerLineKey, dict[str, Any]]]] = {}


def _maps_for_session(session: Session) -> tuple[set[CareerLineKey], dict[CareerLineKey, dict[str, Any]]]:
    lane = id(session.get_bind())
    if lane not in _ADJ_CACHE:
        _ADJ_CACHE.clear()
        _ADJ_CACHE[lane] = _load_adjustment_maps(session)
    return _ADJ_CACHE[lane]


def clear_adjustment_cache() -> None:
    _ADJ_CACHE.clear()


def is_career_line_excluded(session: Session, ln: Any, *, line_kind: str) -> bool:
    excluded, _ = _maps_for_session(session)
    key = _key_from_line(ln, line_kind)
    if key in excluded:
        return True
    wildcard = career_line_key(
        player_id=key[0],
        season_year=key[1],
        team_fhm_id=key[2],
        career_source="*",
        line_kind=line_kind,
    )
    return wildcard in excluded


def apply_career_line_overrides(ln: Any, st: Any, *, session: Session, line_kind: str) -> Any:
    """Return ``st`` namespace with admin override fields merged when configured."""
    _, overrides = _maps_for_session(session)
    for key, payload in overrides.items():
        if not _line_matches_key(key, ln):
            continue
        for attr, val in payload.items():
            if val is None:
                continue
            if hasattr(st, attr):
                setattr(st, attr, val)
            elif attr == "points" and hasattr(st, "goals") and hasattr(st, "assists"):
                try:
                    setattr(st, "points", int(val))
                except (TypeError, ValueError):
                    pass
            elif attr == "goals_against" and hasattr(st, "ga"):
                try:
                    setattr(st, "ga", int(val))
                except (TypeError, ValueError):
                    pass
            elif attr == "shots_against" and hasattr(st, "sa"):
                try:
                    setattr(st, "sa", int(val))
                except (TypeError, ValueError):
                    pass
        break
    return st


def excluded_career_tuples_for_sql(session: Session) -> set[tuple[int, int, str | None]]:
    """(player_id, season_year, team_fhm_id) tuples for SQL exclusion filters."""
    excluded, _ = _maps_for_session(session)
    out: set[tuple[int, int, str | None]] = set()
    for pid, sy, fhm, _src in excluded:
        out.add((pid, sy, fhm))
    return out


def parse_career_adjustment_form(form: Any) -> dict[str, Any]:
    def _opt_int(name: str) -> int | None:
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    override_fields: dict[str, Any] = {}
    for field in (
        "goals",
        "assists",
        "points",
        "gp",
        "pim",
        "shots",
        "wins",
        "losses",
        "shutouts",
        "goals_against",
        "shots_against",
    ):
        raw = (form.get(f"override_{field}") or "").strip()
        if not raw:
            continue
        try:
            if field in ("gp", "goals", "assists", "points", "pim", "shots", "wins", "losses", "shutouts", "goals_against", "shots_against"):
                override_fields[field] = int(raw)
        except ValueError:
            continue

    return {
        "adj_type": (form.get("adj_type") or ADJ_EXCLUDE).strip().lower(),
        "line_kind": (form.get("line_kind") or LINE_SKATER).strip().lower(),
        "player_id": _opt_int("player_id"),
        "season_year": _opt_int("season_year"),
        "team_fhm_id": (form.get("team_fhm_id") or "").strip() or None,
        "career_source": (form.get("career_source") or "").strip() or None,
        "overrides": override_fields or None,
        "notes": (form.get("notes") or "").strip() or None,
    }
