"""Composite 1–100 overall from ABI/POT (0.5–5) plus FHM attribute columns (0–20)."""
from __future__ import annotations

import math
from typing import Any, Iterable

from sqlalchemy import select

# Same “overview” slice as team ratings / prospects / free-agent overview.
SKATER_OVERVIEW_ATTR_KEYS: tuple[str, ...] = (
    "skating",
    "shooting",
    "playmaking",
    "defending",
    "physicality",
    "conditioning",
    "character",
    "hockey_sense",
)
GOALIE_OVERVIEW_ATTR_KEYS: tuple[str, ...] = (
    "g_positioning",
    "g_passing",
    "g_pokecheck",
    "blocker",
    "glove",
    "rebound",
    "recovery",
    "g_puckhandling",
    "low_shots",
    "g_skating",
    "reflexes",
)


def player_is_goalie_for_overall(player: object | None) -> bool:
    pos = (getattr(player, "position", None) or "").strip().upper()
    return pos == "G"


def _parse_rating_cell(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_abi_pot(v: float) -> float:
    return max(0.0, min(1.0, (v - 0.5) / 4.5))


def _norm_attr_20(v: float) -> float:
    return max(0.0, min(1.0, v / 20.0))


def compute_player_overall_100(
    abi: float | None,
    pot: float | None,
    ratings_row: dict[str, Any] | None,
    *,
    is_goalie: bool,
) -> int | None:
    """Return 1–100 composite, or None if nothing to score."""
    keys = GOALIE_OVERVIEW_ATTR_KEYS if is_goalie else SKATER_OVERVIEW_ATTR_KEYS
    parts: list[float] = []
    if abi is not None:
        try:
            parts.append(_norm_abi_pot(float(abi)))
        except (TypeError, ValueError):
            pass
    if pot is not None:
        try:
            parts.append(_norm_abi_pot(float(pot)))
        except (TypeError, ValueError):
            pass
    attr_norms: list[float] = []
    if ratings_row:
        for k in keys:
            fv = _parse_rating_cell(ratings_row.get(k))
            if fv is not None:
                attr_norms.append(_norm_attr_20(fv))
    if attr_norms:
        parts.append(sum(attr_norms) / len(attr_norms))
    if not parts:
        return None
    raw = 1.0 + 99.0 * (sum(parts) / len(parts))
    return int(max(1, min(100, round(raw))))


def overall_cell_bundle(
    player: object,
    ratings_row: dict[str, Any] | None,
    baseline_by_player_id: dict[int, int],
) -> dict[str, Any]:
    """Template-friendly dict: score, delta (vs baseline), has_baseline."""
    is_g = player_is_goalie_for_overall(player)
    pid = int(getattr(player, "id"))
    abi = getattr(player, "overall_ability", None)
    pot = getattr(player, "overall_potential", None)
    score = compute_player_overall_100(abi, pot, ratings_row, is_goalie=is_g)
    base = baseline_by_player_id.get(pid)
    delta: int | None = None
    if score is not None and base is not None:
        delta = int(score) - int(base)
    return {
        "score": score,
        "baseline": base,
        "delta": delta,
        "has_baseline": base is not None,
    }


def overall_cell_map_for_players(
    pairs: Iterable[tuple[object, dict[str, Any] | None]],
    baseline_by_player_id: dict[int, int],
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for pl, rr in pairs:
        pid = int(getattr(pl, "id"))
        out[pid] = overall_cell_bundle(pl, rr, baseline_by_player_id)
    return out


def fetch_overall_baselines_by_player_ids(session: object, player_ids: Iterable[int]) -> dict[int, int]:
    from app.models import PlayerOverallBaseline

    ids = sorted({int(x) for x in player_ids if x is not None})
    if not ids:
        return {}
    rows = session.scalars(select(PlayerOverallBaseline).where(PlayerOverallBaseline.player_id.in_(ids))).all()
    return {int(r.player_id): int(r.baseline_score) for r in rows}


def build_overall_cell_map_from_players(session: object, players: Iterable[object]) -> dict[int, dict[str, Any]]:
    """Convenience: load CSV rating rows and baselines for a roster or player list."""
    from app.services.player_ratings_csv import get_player_ratings_row

    pl_list = list(players)
    pairs = [(pl, get_player_ratings_row(getattr(pl, "fhm_player_id", None))) for pl in pl_list]
    bids = fetch_overall_baselines_by_player_ids(session, [int(getattr(pl, "id")) for pl in pl_list])
    return overall_cell_map_for_players(pairs, bids)


def refresh_all_player_overall_baselines(session: object) -> int:
    """Write current composite OVR (1-100) for every player to ``player_overall_baselines``. Returns rows written."""
    from datetime import datetime

    from sqlalchemy import select

    from app.models import Player, PlayerOverallBaseline
    from app.services.player_ratings_csv import get_player_ratings_row

    n = 0
    for pl in session.scalars(select(Player)):
        rr = get_player_ratings_row(pl.fhm_player_id)
        sc = compute_player_overall_100(
            pl.overall_ability,
            pl.overall_potential,
            rr,
            is_goalie=player_is_goalie_for_overall(pl),
        )
        if sc is None:
            continue
        row = session.get(PlayerOverallBaseline, pl.id)
        if row:
            row.baseline_score = sc
            row.updated_at = datetime.utcnow()
        else:
            session.add(
                PlayerOverallBaseline(
                    player_id=pl.id,
                    baseline_score=sc,
                    updated_at=datetime.utcnow(),
                )
            )
        n += 1
    session.commit()
    return n
