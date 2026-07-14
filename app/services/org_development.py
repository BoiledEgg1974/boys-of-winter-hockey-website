"""Org-level monthly development reports from player rating snapshots."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import OrgDevelopmentReportArchive, Player, PlayerRatingSnapshot, Season, Team
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.org_development_timeline import (
    ORG_DEV_ARCHIVE_MONTH_LIMIT,
    backfill_null_snapshot_timelines,
    development_report_title,
    league_timeline_anchor_date,
    timeline_from_snapshot,
    timeline_sort_key,
)
from app.services.player_development import _ATTR_DELTA_MIN, _ATTR_LABELS, _attr_label
from app.services.player_overall_score import player_is_goalie_for_overall
from app.services.player_rating_avgs import (
    DEF_KEYS,
    MENTAL_KEYS_GOALIE,
    MENTAL_KEYS_SKATER,
    OFF_KEYS,
    OVERVIEW_KEYS,
    PHYS_KEYS,
    _float_cell,
)
from app.services.player_rating_snapshots import (
    GOALIE_ATHLETIC_KEYS,
    GOALIE_CREASE_KEYS,
    GOALIE_PUCK_KEYS,
)
from app.services.player_ratings_csv import player_positions_display_label
from app.services.prospect_system_rankings import resolve_prospect_team_fallbacks

_OVERVIEW_LABELS: dict[str, str] = {
    "skating": "Skating",
    "shooting": "Shooting",
    "playmaking": "Playmaking",
    "defending": "Defending",
    "physicality": "Physicality",
    "conditioning": "Conditioning",
    "character": "Character",
    "hockey_sense": "Hockey sense",
}

_SKATER_ATTR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Skating", ("acceleration", "agility", "balance", "speed")),
    ("Shooting", ("shooting_accuracy", "shooting_range", "screening")),
    ("Playmaking", ("passing", "puck_handling", "getting_open")),
    ("Defending", ("checking", "positioning", "shot_blocking", "stickchecking", "defensive_read", "faceoffs")),
    ("Physicality", ("strength", "hitting", "fighting")),
    ("Conditioning", ("stamina",)),
    ("Character", ("aggression", "bravery", "determination", "teamplayer", "leadership", "temperament", "professionalism")),
    ("Hockey sense", ("offensive_read", "mental_toughness")),
)

_GOALIE_ATTR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Skating", ("g_skating", "reflexes")),
    ("Crease", GOALIE_CREASE_KEYS),
    ("Athletic", GOALIE_ATHLETIC_KEYS),
    ("Puck skills", GOALIE_PUCK_KEYS),
    ("Character", ("aggression", "determination", "teamplayer", "leadership", "professionalism")),
    ("Conditioning", ("goalie_stamina",)),
    ("Hockey sense", ("mental_toughness",)),
)

_RAW_ATTR_KEYS: frozenset[str] = frozenset(
    OFF_KEYS
    + DEF_KEYS
    + MENTAL_KEYS_SKATER
    + MENTAL_KEYS_GOALIE
    + PHYS_KEYS
    + GOALIE_CREASE_KEYS
    + GOALIE_ATHLETIC_KEYS
    + GOALIE_PUCK_KEYS
)


def _parse_ratings(snap: object) -> dict[str, float]:
    try:
        raw = json.loads(getattr(snap, "ratings_json", "") or "{}")
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for key, val in raw.items():
        fv = _float_cell(val)
        if fv is not None:
            out[str(key)] = float(fv)
    return out


def format_signed_delta(delta: float) -> str:
    if delta > 0:
        if abs(delta - round(delta)) < 0.05:
            return f"+{int(round(delta))}"
        return f"+{delta:.1f}".rstrip("0").rstrip(".")
    if delta < 0:
        if abs(delta - round(delta)) < 0.05:
            return f"{int(round(delta))}"
        return f"{delta:.1f}".rstrip("0").rstrip(".")
    return "0"


def _attr_movers(first: dict[str, float], last: dict[str, float]) -> list[dict[str, Any]]:
    movers: list[dict[str, Any]] = []
    keys = sorted(set(first) | set(last))
    for key in keys:
        if key in OVERVIEW_KEYS:
            continue
        if key not in _RAW_ATTR_KEYS and key not in _ATTR_LABELS:
            continue
        a = first.get(key)
        b = last.get(key)
        if a is None or b is None:
            continue
        d = float(b) - float(a)
        if abs(d) < _ATTR_DELTA_MIN:
            continue
        rd = round(d, 1)
        movers.append(
            {
                "key": key,
                "label": _attr_label(key),
                "delta": rd,
                "display": format_signed_delta(rd),
            }
        )
    movers.sort(key=lambda m: (-abs(float(m["delta"])), str(m["label"])))
    return movers


def _category_movers(
    first: dict[str, float],
    last: dict[str, float],
    *,
    is_goalie: bool,
) -> list[dict[str, Any]]:
    cats: list[dict[str, Any]] = []
    overview_present = any(k in first and k in last for k in OVERVIEW_KEYS)
    if overview_present:
        for key in OVERVIEW_KEYS:
            a = first.get(key)
            b = last.get(key)
            if a is None or b is None:
                continue
            d = float(b) - float(a)
            if abs(d) < _ATTR_DELTA_MIN:
                continue
            rd = round(d)
            if rd == 0:
                rd = 1 if d > 0 else -1
            label = _OVERVIEW_LABELS.get(key, key.replace("_", " ").title())
            cats.append(
                {
                    "key": key,
                    "label": label,
                    "delta": float(rd),
                    "display": format_signed_delta(float(rd)),
                }
            )
        if cats:
            cats.sort(key=lambda m: (-abs(float(m["delta"])), str(m["label"])))
            return cats

    buckets = _GOALIE_ATTR_CATEGORIES if is_goalie else _SKATER_ATTR_CATEGORIES
    for label, keys in buckets:
        net = 0.0
        any_move = False
        for key in keys:
            a = first.get(key)
            b = last.get(key)
            if a is None or b is None:
                continue
            d = float(b) - float(a)
            if abs(d) < _ATTR_DELTA_MIN:
                continue
            any_move = True
            net += d
        if not any_move or abs(net) < _ATTR_DELTA_MIN:
            continue
        rd = int(round(net))
        if rd == 0:
            rd = 1 if net > 0 else -1
        cats.append(
            {
                "key": label.lower().replace(" ", "_"),
                "label": label,
                "delta": float(rd),
                "display": format_signed_delta(float(rd)),
            }
        )
    cats.sort(key=lambda m: (-abs(float(m["delta"])), str(m["label"])))
    return cats


def classify_player_month_diff(
    first_ratings: dict[str, float],
    last_ratings: dict[str, float],
    *,
    is_goalie: bool,
    overall: int | None = None,
    ability: float | None = None,
    potential: float | None = None,
) -> dict[str, Any] | None:
    """Return progression/regression card payload, or None if stable."""
    attrs = _attr_movers(first_ratings, last_ratings)
    cats = _category_movers(first_ratings, last_ratings, is_goalie=is_goalie)
    improved = [a for a in attrs if float(a["delta"]) > 0]
    regressed = [a for a in attrs if float(a["delta"]) < 0]
    if not improved and not regressed and not cats:
        return None
    cat_up = sum(1 for c in cats if float(c["delta"]) > 0)
    cat_down = sum(1 for c in cats if float(c["delta"]) < 0)
    up_score = len(improved) + cat_up
    down_score = len(regressed) + cat_down
    if up_score > down_score:
        side = "progression"
    elif down_score > up_score:
        side = "regression"
    elif improved or cat_up:
        side = "progression"
    elif regressed or cat_down:
        side = "regression"
    else:
        return None
    return {
        "side": side,
        "categories": cats,
        "attributes": attrs,
        "overall": overall,
        "ability": ability,
        "potential": potential,
        "improved_count": len(improved),
        "regressed_count": len(regressed),
    }


def resolve_org_players(
    session: Session,
    team: Team,
    season: Season | None,
) -> list[Player]:
    league_ids = bowl_nhl_league_ids(session)
    players = session.scalars(
        select(Player)
        .options(joinedload(Player.current_team))
        .where(Player.retired.is_(False))
    ).unique().all()
    resolved = resolve_prospect_team_fallbacks(session, players, season)

    def effective_team(pl: Player) -> Team | None:
        return pl.current_team or resolved.get(pl.id)

    out: list[Player] = []
    for pl in players:
        eff = effective_team(pl)
        if not eff or int(eff.id) != int(team.id):
            continue
        if pl.current_team_id != team.id and eff.fhm_league_id not in league_ids:
            continue
        out.append(pl)
    out.sort(key=lambda p: ((p.last_name or "").lower(), (p.first_name or "").lower(), p.id))
    return out


def _serialize_report_entry(entry: dict[str, Any]) -> dict[str, Any]:
    pl = entry["player"]
    return {
        "player_id": int(pl.id),
        "player_name": pl.full_name or "",
        "position": entry.get("position") or "",
        "age": entry.get("age"),
        "overall": entry.get("overall"),
        "ability": entry.get("ability"),
        "potential": entry.get("potential"),
        "categories": entry.get("categories") or [],
        "attributes": entry.get("attributes") or [],
    }


def _hydrate_report_entries(
    session: Session,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [int(r["player_id"]) for r in rows if r.get("player_id") is not None]
    if not ids:
        return []
    players = session.scalars(select(Player).where(Player.id.in_(ids))).all()
    by_id = {int(p.id): p for p in players}
    out: list[dict[str, Any]] = []
    for row in rows:
        pl = by_id.get(int(row["player_id"]))
        if pl is None:
            continue
        out.append(
            {
                "player": pl,
                "position": row.get("position") or "",
                "age": row.get("age"),
                "overall": row.get("overall"),
                "ability": row.get("ability"),
                "potential": row.get("potential"),
                "categories": row.get("categories") or [],
                "attributes": row.get("attributes") or [],
            }
        )
    return out


def _sort_player_cards(rows: list[dict[str, Any]]) -> None:
    rows.sort(
        key=lambda r: (
            -len(r.get("attributes") or []),
            (getattr(r.get("player"), "full_name", None) or "").lower(),
        )
    )


def _archive_sort_key(row: object) -> tuple[int, int]:
    return timeline_sort_key(
        int(getattr(row, "timeline_season_start_year")),
        int(getattr(row, "timeline_calendar_year")),
        int(getattr(row, "timeline_calendar_month")),
    )


def _compute_live_reports_from_snapshots(
    session: Session,
    team: Team,
    *,
    season: Season | None,
    age_ref: date | None,
    player_age_fn: Callable[[date | None, date | None], int | None],
) -> list[dict[str, Any]]:
    players = resolve_org_players(session, team, season)
    if not players:
        return []

    anchor = league_timeline_anchor_date(session, season)
    player_ids = [int(p.id) for p in players]
    player_by_id = {int(p.id): p for p in players}
    snaps = session.scalars(
        select(PlayerRatingSnapshot)
        .where(PlayerRatingSnapshot.player_id.in_(player_ids))
        .order_by(
            PlayerRatingSnapshot.player_id.asc(),
            PlayerRatingSnapshot.snapshot_at.asc(),
            PlayerRatingSnapshot.id.asc(),
        )
    ).all()

    by_player_timeline: dict[int, dict[str, PlayerRatingSnapshot]] = defaultdict(dict)
    timeline_meta: dict[str, dict[str, Any]] = {}
    for snap in snaps:
        tl = timeline_from_snapshot(snap, fallback_anchor=anchor)
        tkey = str(tl["timeline_key"])
        timeline_meta[tkey] = tl
        pid = int(snap.player_id)
        prev = by_player_timeline[pid].get(tkey)
        if prev is None or (snap.snapshot_at, snap.id) >= (prev.snapshot_at, prev.id):
            by_player_timeline[pid][tkey] = snap

    months_sorted = sorted(timeline_meta.keys(), key=lambda k: timeline_meta[k]["sort_key"])
    if len(months_sorted) < 2:
        return []

    reports: list[dict[str, Any]] = []
    for i in range(1, len(months_sorted)):
        curr_key = months_sorted[i]
        prev_key = months_sorted[i - 1]
        curr_tl = timeline_meta[curr_key]
        progression: list[dict[str, Any]] = []
        regression: list[dict[str, Any]] = []
        for pid, timeline_map in by_player_timeline.items():
            curr = timeline_map.get(curr_key)
            prev = timeline_map.get(prev_key)
            if curr is None or prev is None:
                continue
            pl = player_by_id.get(pid)
            if pl is None:
                continue
            card = classify_player_month_diff(
                _parse_ratings(prev),
                _parse_ratings(curr),
                is_goalie=player_is_goalie_for_overall(pl),
                overall=int(curr.overall_score) if curr.overall_score is not None else None,
                ability=float(curr.ability) if curr.ability is not None else None,
                potential=float(curr.potential) if curr.potential is not None else None,
            )
            if card is None:
                continue
            entry = {
                "player": pl,
                "position": player_positions_display_label(pl) or (pl.position or ""),
                "age": player_age_fn(pl.birth_date, age_ref),
                "overall": card["overall"],
                "ability": card["ability"],
                "potential": card["potential"],
                "categories": card["categories"],
                "attributes": card["attributes"],
            }
            if card["side"] == "progression":
                progression.append(entry)
            else:
                regression.append(entry)

        _sort_player_cards(progression)
        _sort_player_cards(regression)
        if not progression and not regression:
            continue
        sy = int(curr_tl["timeline_season_start_year"])
        cy = int(curr_tl["timeline_calendar_year"])
        cm = int(curr_tl["timeline_calendar_month"])
        reports.append(
            {
                "timeline_key": curr_key,
                "timeline_season_start_year": sy,
                "timeline_calendar_year": cy,
                "timeline_calendar_month": cm,
                "sort_key": timeline_sort_key(sy, cy, cm),
                "year": cy,
                "month": cm,
                "month_key": curr_key,
                "label": development_report_title(
                    calendar_year=cy,
                    calendar_month=cm,
                    season_start_year=sy,
                ),
                "progression": progression,
                "regression": regression,
            }
        )
    return reports


def persist_team_org_development_reports(
    session: Session,
    team: Team,
    *,
    league_slug: str,
    season: Season | None,
    age_ref: date | None,
    player_age_fn: Callable[[date | None, date | None], int | None],
) -> int:
    live = _compute_live_reports_from_snapshots(
        session,
        team,
        season=season,
        age_ref=age_ref,
        player_age_fn=player_age_fn,
    )
    if not live:
        return 0

    slug = (league_slug or "").strip()
    upserted = 0
    for report in live:
        payload = {
            "progression": [_serialize_report_entry(e) for e in report.get("progression") or []],
            "regression": [_serialize_report_entry(e) for e in report.get("regression") or []],
        }
        tkey = str(report["timeline_key"])
        existing = session.scalars(
            select(OrgDevelopmentReportArchive)
            .where(
                OrgDevelopmentReportArchive.team_id == int(team.id),
                OrgDevelopmentReportArchive.timeline_key == tkey,
            )
            .limit(1)
        ).first()
        if existing is None:
            session.add(
                OrgDevelopmentReportArchive(
                    team_id=int(team.id),
                    league_slug=slug,
                    timeline_key=tkey,
                    timeline_season_start_year=int(report["timeline_season_start_year"]),
                    timeline_calendar_year=int(report["timeline_calendar_year"]),
                    timeline_calendar_month=int(report["timeline_calendar_month"]),
                    label=str(report["label"]),
                    report_json=json.dumps(payload, sort_keys=True),
                )
            )
        else:
            existing.label = str(report["label"])
            existing.report_json = json.dumps(payload, sort_keys=True)
            existing.archived_at = datetime.utcnow()
            existing.league_slug = slug
        upserted += 1

    session.flush()
    archived = list(
        session.scalars(
            select(OrgDevelopmentReportArchive).where(
                OrgDevelopmentReportArchive.team_id == int(team.id)
            )
        ).all()
    )
    archived.sort(key=_archive_sort_key, reverse=True)
    for stale in archived[ORG_DEV_ARCHIVE_MONTH_LIMIT:]:
        session.delete(stale)
    if upserted:
        session.commit()
    return upserted


def _purge_stale_org_development_archives(session: Session, *, league_slug: str) -> int:
    """Remove archives whose months no longer match stamped snapshot timelines."""
    slug = (league_slug or "").strip()
    if not slug:
        return 0
    valid_keys = {
        f"{int(cy):04d}-{int(cm):02d}"
        for cy, cm in session.execute(
            select(
                PlayerRatingSnapshot.timeline_calendar_year,
                PlayerRatingSnapshot.timeline_calendar_month,
            ).where(PlayerRatingSnapshot.timeline_calendar_year.isnot(None))
        ).all()
        if cy is not None and cm is not None
    }
    rows = list(
        session.scalars(
            select(OrgDevelopmentReportArchive).where(OrgDevelopmentReportArchive.league_slug == slug)
        ).all()
    )
    deleted = 0
    for row in rows:
        if str(row.timeline_key) not in valid_keys:
            session.delete(row)
            deleted += 1
    if deleted:
        session.flush()
    return deleted


def persist_org_development_reports_for_league(session: Session, league_slug: str) -> int:
    from app.services.seasons import get_current_season, season_age_reference_date

    slug = (league_slug or "").strip()
    if not slug:
        return 0
    backfilled = backfill_null_snapshot_timelines(session)
    purged = _purge_stale_org_development_archives(session, league_slug=slug)
    if backfilled or purged:
        session.commit()
    league_ids = bowl_nhl_league_ids(session)
    teams = session.scalars(
        select(Team).where(Team.fhm_league_id.in_(league_ids)).order_by(Team.name)
    ).all()
    season = get_current_season()
    age_ref = season_age_reference_date(season)

    def _age_fn(birth: date | None, ref: date | None) -> int | None:
        if birth is None:
            return None
        ref_d = ref if ref is not None else date.today()
        return ref_d.year - birth.year - ((ref_d.month, ref_d.day) < (birth.month, birth.day))

    total = 0
    for team in teams:
        total += persist_team_org_development_reports(
            session,
            team,
            league_slug=slug,
            season=season,
            age_ref=age_ref,
            player_age_fn=_age_fn,
        )
    return total


def _load_archived_reports(session: Session, team_id: int) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(OrgDevelopmentReportArchive).where(
                OrgDevelopmentReportArchive.team_id == int(team_id)
            )
        ).all()
    )
    rows.sort(key=_archive_sort_key, reverse=True)
    out: list[dict[str, Any]] = []
    for row in rows[:ORG_DEV_ARCHIVE_MONTH_LIMIT]:
        try:
            payload = json.loads(row.report_json or "{}")
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "timeline_key": row.timeline_key,
                "timeline_season_start_year": int(row.timeline_season_start_year),
                "timeline_calendar_year": int(row.timeline_calendar_year),
                "timeline_calendar_month": int(row.timeline_calendar_month),
                "year": int(row.timeline_calendar_year),
                "month": int(row.timeline_calendar_month),
                "month_key": row.timeline_key,
                "label": row.label,
                "progression": _hydrate_report_entries(session, payload.get("progression") or []),
                "regression": _hydrate_report_entries(session, payload.get("regression") or []),
                "archived": True,
            }
        )
    return out


def build_org_development_reports(
    session: Session,
    team: Team,
    *,
    season: Season | None,
    age_ref: date | None,
    player_age_fn: Callable[[date | None, date | None], int | None],
    league_slug: str = "",
) -> dict[str, Any]:
    players = resolve_org_players(session, team, season)
    if not players:
        return {
            "reports": [],
            "has_history": False,
            "empty_reason": "No players linked to this organization.",
            "archive_limit": ORG_DEV_ARCHIVE_MONTH_LIMIT,
        }

    slug = (league_slug or "").strip()
    if slug:
        backfilled = backfill_null_snapshot_timelines(session)
        purged = _purge_stale_org_development_archives(session, league_slug=slug)
        if backfilled or purged:
            session.commit()
        persist_team_org_development_reports(
            session,
            team,
            league_slug=slug,
            season=season,
            age_ref=age_ref,
            player_age_fn=player_age_fn,
        )

    reports = _load_archived_reports(session, int(team.id))
    if reports:
        return {
            "reports": reports,
            "has_history": True,
            "empty_reason": None,
            "archive_limit": ORG_DEV_ARCHIVE_MONTH_LIMIT,
        }

    live = _compute_live_reports_from_snapshots(
        session,
        team,
        season=season,
        age_ref=age_ref,
        player_age_fn=player_age_fn,
    )
    live.sort(key=lambda r: r.get("sort_key") or (0, 0), reverse=True)
    live = live[:ORG_DEV_ARCHIVE_MONTH_LIMIT]
    if live:
        return {
            "reports": live,
            "has_history": True,
            "empty_reason": None,
            "archive_limit": ORG_DEV_ARCHIVE_MONTH_LIMIT,
        }
    return {
        "reports": [],
        "has_history": False,
        "empty_reason": (
            "Need another in-game month to build development reports. "
            "Reports appear after ratings are captured across at least two league months."
        ),
        "archive_limit": ORG_DEV_ARCHIVE_MONTH_LIMIT,
    }
