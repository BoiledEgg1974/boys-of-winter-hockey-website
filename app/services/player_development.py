"""Player profile Development panel: rating snapshots, category tabs, trend charts."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy.orm import Session

from app.services.player_overall_score import player_is_goalie_for_overall
from app.services.player_rating_avgs import (
    DEF_KEYS,
    MENTAL_KEYS_GOALIE,
    MENTAL_KEYS_SKATER,
    OFF_KEYS,
    PHYS_KEYS,
    _float_cell,
)
from app.services.player_rating_snapshots import (
    GOALIE_ATHLETIC_KEYS,
    GOALIE_CREASE_KEYS,
    GOALIE_PUCK_KEYS,
    load_player_rating_snapshots,
    seed_player_rating_snapshot_if_needed,
    tracked_rating_keys,
)
from app.services.player_ratings_csv import fhm_abi_pot_float

_ATTR_DELTA_MIN = 0.5

_ATTR_LABELS: dict[str, str] = {
    "screening": "Screening",
    "getting_open": "Getting open",
    "passing": "Passing",
    "puck_handling": "Puck handling",
    "shooting_accuracy": "Shooting accuracy",
    "shooting_range": "Shooting range",
    "offensive_read": "Offensive read",
    "checking": "Checking",
    "faceoffs": "Faceoffs",
    "hitting": "Hitting",
    "positioning": "Positioning",
    "shot_blocking": "Shot blocking",
    "stickchecking": "Stickchecking",
    "defensive_read": "Defensive read",
    "aggression": "Aggression",
    "bravery": "Bravery",
    "determination": "Determination",
    "teamplayer": "Team Player",
    "leadership": "Leadership",
    "temperament": "Temperament",
    "professionalism": "Professionalism",
    "mental_toughness": "Mental Toughness",
    "goalie_stamina": "Stamina",
    "acceleration": "Acceleration",
    "agility": "Agility",
    "balance": "Balance",
    "speed": "Speed",
    "stamina": "Stamina",
    "strength": "Strength",
    "fighting": "Fighting",
    "g_positioning": "Positioning",
    "g_passing": "Passing",
    "g_pokecheck": "Pokecheck",
    "blocker": "Blocker",
    "glove": "Glove",
    "rebound": "Rebound",
    "recovery": "Recovery",
    "g_puckhandling": "Puckhandling",
    "low_shots": "Low Shots",
    "g_skating": "Skating",
    "reflexes": "Reflexes",
    "goalie_technique": "Goalie Technique",
    "goalie_overall_positioning": "Goalie Overall Positioning",
}

_SKATER_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("summary", "Summary", ()),
    ("offense", "Offense", OFF_KEYS),
    ("defense", "Defense", DEF_KEYS),
    ("mental", "Mental", MENTAL_KEYS_SKATER),
    ("physical", "Physical", PHYS_KEYS),
)

_GOALIE_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("summary", "Summary", ()),
    ("crease", "Crease", GOALIE_CREASE_KEYS),
    ("athletic", "Athletic", GOALIE_ATHLETIC_KEYS),
    ("puck_skills", "Puck Skills", GOALIE_PUCK_KEYS),
    ("mental", "Mental", MENTAL_KEYS_GOALIE),
)


def _trend_status(delta: float | None) -> tuple[str, str]:
    if delta is None:
        return "pending", "Current only"
    if delta >= _ATTR_DELTA_MIN:
        return "up", f"Progressing +{delta:.1f}"
    if delta <= -_ATTR_DELTA_MIN:
        return "down", f"Regressing {delta:.1f}"
    return "flat", "Stable"


def _attr_label(key: str) -> str:
    return _ATTR_LABELS.get(key, key.replace("_", " ").title())


def _parse_snapshot_ratings(snap: object) -> dict[str, float]:
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


def _chart_bundle(points: list[dict[str, Any]]) -> dict[str, Any]:
    if len(points) < 2:
        current = points[-1]["y"] if points else None
        return {
            "has_trend": False,
            "current": current,
            "start": current,
            "delta": None,
            "status_kind": "pending",
            "status_label": "Current only",
            "range_label": "Need one more import",
            "message": (
                "This is the current rating. Progression/regression will appear after the next import captures another snapshot."
                if len(points) == 1
                else "No rating snapshot has been captured yet."
            ),
            "svg": None,
        }
    ys = [float(p["y"]) for p in points]
    y_min = min(ys)
    y_max = max(ys)
    pad = 0.75 if y_max == y_min else max(0.75, (y_max - y_min) * 0.12)
    lo = y_min - pad
    hi = y_max + pad
    span = hi - lo or 1.0
    w, h = 200.0, 56.0
    n = len(points)
    coords: list[tuple[float, float]] = []
    for i, y in enumerate(ys):
        x = 6.0 + (w - 12.0) * (i / max(1, n - 1))
        cy = h - 6.0 - ((float(y) - lo) / span) * (h - 12.0)
        coords.append((x, cy))
    path_d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    dots = [{"cx": x, "cy": y} for x, y in coords]
    delta = round(ys[-1] - ys[0], 1)
    status_kind, status_label = _trend_status(delta)
    return {
        "has_trend": True,
        "current": ys[-1],
        "start": ys[0],
        "delta": delta,
        "status_kind": status_kind,
        "status_label": status_label,
        "range_label": f"{points[0].get('label') or 'Start'} to {points[-1].get('label') or 'Now'}",
        "message": None,
        "svg": {"width": int(w), "height": int(h), "path_d": path_d, "dots": dots},
        "labels": [p.get("label") or "" for p in points],
    }


def _attr_series(snapshots: list[object], key: str) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for snap in snapshots:
        ratings = _parse_snapshot_ratings(snap)
        val = ratings.get(key)
        if val is None:
            continue
        at = getattr(snap, "snapshot_at", None)
        label = at.strftime("%b %Y") if isinstance(at, datetime) else ""
        points.append({"label": label, "y": float(val)})
    return _chart_bundle(points)


def _metric_delta(first: float | None, last: float | None) -> float | None:
    if first is None or last is None:
        return None
    return round(float(last) - float(first), 1)


def _summary_changes(
    snapshots: list[object],
    keys: tuple[str, ...],
    *,
    current_row: dict[str, Any] | None,
) -> dict[str, Any]:
    if len(snapshots) < 2:
        return {
            "has_trend": False,
            "headline": "Tracking starts with this snapshot",
            "window_label": "Need one more import",
            "attrs_changed": 0,
            "improved_count": 0,
            "regressed_count": 0,
            "stable_count": len(keys),
            "overall_delta": None,
            "ability_delta": None,
            "potential_delta": None,
            "risers": [],
            "fallers": [],
            "note": (
                "Current snapshot only. After the next import, this panel will compare the newest ratings against this baseline and label each attribute as progressing, regressing, or stable."
                if len(snapshots) == 1
                else "Import rating snapshots to track development over time."
            ),
        }
    first, last = snapshots[0], snapshots[-1]
    first_ratings = _parse_snapshot_ratings(first)
    last_ratings = _parse_snapshot_ratings(last)
    deltas: list[tuple[str, float]] = []
    for key in keys:
        a = first_ratings.get(key)
        b = last_ratings.get(key)
        if a is None or b is None:
            continue
        d = float(b) - float(a)
        if abs(d) >= _ATTR_DELTA_MIN:
            deltas.append((key, round(d, 1)))
    risers = sorted([d for d in deltas if d[1] > 0], key=lambda x: x[1], reverse=True)[:3]
    fallers = sorted([d for d in deltas if d[1] < 0], key=lambda x: x[1])[:3]
    improved_count = len([d for d in deltas if d[1] > 0])
    regressed_count = len([d for d in deltas if d[1] < 0])
    stable_count = max(0, len(keys) - len(deltas))
    overall_delta = _metric_delta(
        getattr(first, "overall_score", None),
        getattr(last, "overall_score", None),
    )
    ability_delta = _metric_delta(getattr(first, "ability", None), getattr(last, "ability", None))
    potential_delta = _metric_delta(getattr(first, "potential", None), getattr(last, "potential", None))
    note_parts: list[str] = []
    if overall_delta is not None and overall_delta != 0:
        direction = "rose" if overall_delta > 0 else "dipped"
        note_parts.append(f"Overall rating {direction} {abs(overall_delta):.0f} point(s)")
    if risers:
        top = risers[0]
        note_parts.append(f"{_attr_label(top[0])} improved +{top[1]:.1f}")
    if fallers:
        bot = fallers[0]
        note_parts.append(f"{_attr_label(bot[0])} declined {bot[1]:.1f}")
    if not note_parts and current_row:
        note_parts.append("Ratings are stable across the tracked window.")
    first_at = getattr(first, "snapshot_at", None)
    last_at = getattr(last, "snapshot_at", None)
    start_label = first_at.strftime("%b %Y") if isinstance(first_at, datetime) else "first snapshot"
    end_label = last_at.strftime("%b %Y") if isinstance(last_at, datetime) else "latest snapshot"
    return {
        "has_trend": True,
        "headline": f"{len(deltas)} moved: {improved_count} up, {regressed_count} down, {stable_count} stable",
        "window_label": f"{start_label} to {end_label}",
        "attrs_changed": len(deltas),
        "improved_count": improved_count,
        "regressed_count": regressed_count,
        "stable_count": stable_count,
        "overall_delta": overall_delta,
        "ability_delta": ability_delta,
        "potential_delta": potential_delta,
        "risers": [{"key": k, "label": _attr_label(k), "delta": d} for k, d in risers],
        "fallers": [{"key": k, "label": _attr_label(k), "delta": d} for k, d in fallers],
        "note": ". ".join(note_parts) + ("." if note_parts else "Tracked ratings unchanged in this window."),
    }


def _tab_attributes(
    snapshots: list[object],
    keys: tuple[str, ...],
    ratings_row: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for key in keys:
        current = _float_cell(ratings_row.get(key)) if ratings_row else None
        series = _attr_series(snapshots, key)
        cards.append(
            {
                "key": key,
                "label": _attr_label(key),
                "current": current,
                "baseline": series.get("start"),
                "delta": series.get("delta"),
                "status_kind": series.get("status_kind", "pending"),
                "status_label": series.get("status_label", "Current only"),
                "range_label": series.get("range_label", "Need one more import"),
                "chart": series,
                "expanded": series.get("has_trend", False),
            }
        )
    return cards


def build_player_development_panel(
    session: Session,
    player: object,
    *,
    ratings_row: dict[str, Any] | None,
    is_goalie: bool,
    hero_abi: float | None = None,
    hero_pot: float | None = None,
    player_ovr: int | None = None,
    retired: bool = False,
    league_slug: str | None = None,
) -> dict[str, Any]:
    slug = (league_slug or "").strip()
    if not slug and has_app_context():
        slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    pid = int(getattr(player, "id"))
    is_g = bool(is_goalie) or player_is_goalie_for_overall(player)
    if retired and not ratings_row:
        return {"enabled": False}
    if not ratings_row:
        return {"enabled": False}

    seed_player_rating_snapshot_if_needed(
        session, player, ratings_row, league_slug=slug  # type: ignore[arg-type]
    )
    snapshots = load_player_rating_snapshots(session, pid)
    keys = tracked_rating_keys(is_goalie=is_g)
    summary = _summary_changes(snapshots, keys, current_row=ratings_row)
    tabs_def = _GOALIE_TABS if is_g else _SKATER_TABS
    tabs: list[dict[str, Any]] = []
    for idx, (slug_id, title, tab_keys) in enumerate(tabs_def):
        if slug_id == "summary":
            tabs.append(
                {
                    "id": slug_id,
                    "title": title,
                    "index": idx,
                    "summary": summary,
                    "snapshot_count": len(snapshots),
                    "ability": hero_abi if hero_abi is not None else fhm_abi_pot_float(ratings_row.get("ability")),
                    "potential": hero_pot if hero_pot is not None else fhm_abi_pot_float(ratings_row.get("potential")),
                    "overall": player_ovr,
                }
            )
        else:
            tabs.append(
                {
                    "id": slug_id,
                    "title": title,
                    "index": idx,
                    "attributes": _tab_attributes(snapshots, tab_keys, ratings_row),
                }
            )

    meta_bits: list[str] = []
    if summary.get("has_trend"):
        changed = int(summary.get("attrs_changed") or 0)
        meta_bits.append(f"{changed} attribute{'s' if changed != 1 else ''} moved (1y)")
    else:
        meta_bits.append("Awaiting next import")
    if player_ovr is not None:
        meta_bits.append(f"OVR {player_ovr}")

    role_note = "Goalie crease and reflex trends" if is_g else "Skater attribute development"
    return {
        "enabled": True,
        "is_goalie": is_g,
        "summary_meta": " · ".join(meta_bits),
        "role_note": role_note,
        "tracking_help": (
            "Current ratings come from the latest player_ratings.csv. "
            "Progression/regression compares the first and newest snapshots captured in the last 365 days."
        ),
        "tabs": tabs,
        "tab_count": len(tabs),
        "has_trend": bool(summary.get("has_trend")),
    }
