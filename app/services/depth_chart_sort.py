"""Depth chart row ordering: highest composite OVR first, then ABI/POT."""
from __future__ import annotations

from typing import Any

from app.services.player_overall_score import compute_player_overall_100
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row


def depth_chart_player_sort_key(
    player: object,
    *,
    bucket: str,
    ratings_row: dict[str, Any] | None = None,
) -> tuple[float, float, float, str]:
    """Return a descending sort key for one depth-chart position column."""
    rr = ratings_row if ratings_row is not None else get_player_ratings_row(
        getattr(player, "fhm_player_id", None)
    )
    abi_f = (
        float(player.overall_ability)
        if getattr(player, "overall_ability", None) is not None
        else None
    )
    if abi_f is None and rr:
        abi_f = fhm_abi_pot_float(rr.get("ability"))
    pot_f = (
        float(player.overall_potential)
        if getattr(player, "overall_potential", None) is not None
        else None
    )
    if pot_f is None and rr:
        pot_f = fhm_abi_pot_float(rr.get("potential"))
    abi = abi_f if abi_f is not None else -1.0
    pot = pot_f if pot_f is not None else -1.0
    ovr = compute_player_overall_100(
        abi_f,
        pot_f,
        rr,
        is_goalie=(bucket == "G"),
    )
    ovr_key = float(ovr) if ovr is not None else -1.0
    name = str(getattr(player, "full_name", "") or "").lower()
    return (ovr_key, abi, pot, name)
