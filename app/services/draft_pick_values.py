"""PuckPedia Perri-style draft pick values (local table, no live external calls)."""
from __future__ import annotations

from dataclasses import dataclass

PERRI_ATTRIBUTION = (
    "Pick values based on the PuckPedia Perri Pick Value model by Matt Perri "
    "(former Director of Hockey Analytics, Arizona Coyotes)."
)
PERRI_ATTRIBUTION_URL = "https://puckpedia.com/pickvalue"
PERRI_METHODOLOGY_URL = "https://puckpedia.com/PerriPickValue"

# Published anchor points from PuckPedia / Perri methodology articles.
_KNOWN_ANCHORS: dict[int, float] = {
    1: 100.0,
    2: 72.69,
    3: 62.07,
    14: 26.46,
    32: 10.38,
    49: 5.19,
    50: 5.0,
}

# Power-law exponent fitted to early-round anchors (pick 1 = 100).
_POWER_EXPONENT = 0.458


def _generated_pick_value(overall_pick: int) -> float:
    pick = max(1, int(overall_pick))
    if pick in _KNOWN_ANCHORS:
        return float(_KNOWN_ANCHORS[pick])
    raw = 100.0 / (pick**_POWER_EXPONENT)
    if pick >= 25:
        # Late first round and beyond: scale toward published 32nd-overall anchor.
        anchor_32 = 100.0 / (32**_POWER_EXPONENT)
        scale = 10.38 / anchor_32 if anchor_32 > 0 else 1.0
        raw *= scale
    return round(raw, 2)


def _build_pick_table(max_pick: int = 224) -> dict[int, float]:
    return {i: _generated_pick_value(i) for i in range(1, max_pick + 1)}


_PICK_TABLE = _build_pick_table(224)

ROUND_AVERAGE_VALUE: dict[int, float] = {}
for _round in range(1, 8):
    start = ((_round - 1) * 32) + 1
    end = _round * 32
    vals = [_PICK_TABLE[p] for p in range(start, min(end, 224) + 1)]
    ROUND_AVERAGE_VALUE[_round] = round(sum(vals) / len(vals), 2) if vals else 0.0

FIRST_ROUND_BUCKET_RANGES: tuple[tuple[str, int, int], ...] = (
    ("picks_1_5", 1, 5),
    ("picks_6_16", 6, 16),
    ("picks_17_32", 17, 32),
)

FIRST_ROUND_BUCKET_VALUE: dict[str, float] = {}
for key, lo, hi in FIRST_ROUND_BUCKET_RANGES:
    vals = [_PICK_TABLE[p] for p in range(lo, hi + 1)]
    FIRST_ROUND_BUCKET_VALUE[key] = round(sum(vals) / len(vals), 2) if vals else 0.0


def first_round_bucket_for_pick_position(pick_position: int) -> str:
    pos = max(1, int(pick_position))
    if pos <= 5:
        return "picks_1_5"
    if pos <= 16:
        return "picks_6_16"
    return "picks_17_32"


def perri_pick_value_exact(overall_pick: int) -> float:
    """Value for a specific overall pick (1–224)."""
    pick = max(1, min(224, int(overall_pick)))
    return float(_PICK_TABLE.get(pick, 0.0))


def perri_pick_value_for_round(*, round_no: int, order_known: bool = False) -> float:
    """Average round value when the exact slot is unknown (rounds 2–7)."""
    rnd = max(1, min(7, int(round_no)))
    if rnd == 1 and not order_known:
        return float(FIRST_ROUND_BUCKET_VALUE["picks_17_32"])
    return float(ROUND_AVERAGE_VALUE.get(rnd, 0.0))


def perri_pick_value_for_first_round_bucket(bucket_key: str) -> float:
    return float(FIRST_ROUND_BUCKET_VALUE.get(str(bucket_key or ""), 0.0))


def perri_pick_value_for_asset(
    *,
    overall_pick: int | None = None,
    round_no: int,
    original_round1_position: int | None = None,
    order_known: bool = False,
) -> float:
    """Resolve a pick's Perri value using specific slot, bucket, or round average."""
    rnd = max(1, int(round_no))
    if overall_pick is not None and int(overall_pick) > 0:
        return perri_pick_value_exact(int(overall_pick))
    if rnd == 1 and original_round1_position is not None:
        bucket = first_round_bucket_for_pick_position(int(original_round1_position))
        return perri_pick_value_for_first_round_bucket(bucket)
    if rnd == 1:
        return perri_pick_value_for_first_round_bucket("picks_17_32")
    return perri_pick_value_for_round(round_no=rnd, order_known=order_known)


@dataclass(frozen=True)
class PickValueAttribution:
    text: str
    calculator_url: str
    methodology_url: str


def pick_value_attribution() -> PickValueAttribution:
    return PickValueAttribution(
        text=PERRI_ATTRIBUTION,
        calculator_url=PERRI_ATTRIBUTION_URL,
        methodology_url=PERRI_METHODOLOGY_URL,
    )
