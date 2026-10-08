"""Disk-backed cache for expensive team page SSR payloads (Monte Carlo, shot quality)."""
from __future__ import annotations

from typing import Any

from flask import current_app

from app.models import Team
from app.services.league_json_cache import (
    DEFAULT_FRESH_TTL_SECONDS,
    DEFAULT_STALE_TTL_SECONDS,
    fresh_ttl_from_config,
    get_or_build_cached_json_swr,
    stale_ttl_from_config,
)

_MC_NAMESPACE = "team_page_mc"
_SQ_NAMESPACE = "team_shot_quality"
_MC_NONE_SENTINEL = "__team_page_mc_none__"


def _fresh_stale(namespace: str) -> tuple[float, float]:
    app = current_app
    fresh = fresh_ttl_from_config(
        app,
        namespace,
        default=DEFAULT_FRESH_TTL_SECONDS[namespace],
    )
    stale = stale_ttl_from_config(
        app,
        namespace,
        default=DEFAULT_STALE_TTL_SECONDS[namespace],
    )
    return fresh, stale


def get_team_page_mc_bundle_cached(
    session,
    season_id: int,
    team_id: int,
    teams_by_id: dict[int, Team],
    *,
    n_sims: int = 800,
) -> dict[str, Any] | None:
    """Cached wrapper for :func:`postseason_odds.build_team_page_mc_bundle`."""
    from app.services.postseason_odds import build_team_page_mc_bundle

    sid = int(season_id)
    tid = int(team_id)
    sims = int(n_sims)

    def builder() -> dict[str, Any]:
        result = build_team_page_mc_bundle(
            session, sid, tid, teams_by_id, n_sims=sims
        )
        if result is None:
            return {"_sentinel": _MC_NONE_SENTINEL}
        return result

    fresh, stale = _fresh_stale(_MC_NAMESPACE)
    body, _status = get_or_build_cached_json_swr(
        _MC_NAMESPACE,
        (sid, tid, sims),
        fresh_ttl=fresh,
        stale_ttl=stale,
        builder=builder,
    )
    if body.get("_sentinel") == _MC_NONE_SENTINEL:
        return None
    return body


def get_team_shot_quality_payload_cached(
    session,
    team: Team,
    season_id: int,
    *,
    segment: str = "rs",
    min_shots: int | None = None,
) -> dict[str, Any]:
    """Cached wrapper for :func:`advanced_stats.build_team_shot_quality_payload`."""
    from app.services.advanced_stats import (
        MIN_SHOT_QUALITY_SHOTS,
        build_team_shot_quality_payload,
    )

    team_id = int(team.id)
    sid = int(season_id)
    seg = str(segment or "rs").strip().lower()
    if seg not in ("rs", "ps", "po"):
        seg = "rs"
    min_s = int(min_shots if min_shots is not None else MIN_SHOT_QUALITY_SHOTS)

    def builder() -> dict[str, Any]:
        payload = build_team_shot_quality_payload(
            session,
            team,
            sid,
            segment=seg,
            min_shots=min_s,
        )
        return {k: v for k, v in payload.items() if k != "team"}

    fresh, stale = _fresh_stale(_SQ_NAMESPACE)
    body, _status = get_or_build_cached_json_swr(
        _SQ_NAMESPACE,
        (team_id, sid, seg, min_s),
        fresh_ttl=fresh,
        stale_ttl=stale,
        builder=builder,
    )
    return {**body, "team": team}
