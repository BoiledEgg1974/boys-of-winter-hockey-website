"""Player snapshot cards (team lines / BOWL Six lineup reveal)."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.models import Player
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_ratings_csv import get_player_ratings_row
from app.services.seasons import season_age_reference_date


def player_is_goalie_position(player: Player) -> bool:
    raw = (player.position or "").strip().upper().replace("/", " ")
    first = raw.split()[0] if raw else ""
    return first == "G"


def player_age_years(birth_date: date | None, as_of: date | None = None) -> int | None:
    if birth_date is None:
        return None
    ref = as_of if as_of is not None else date.today()
    return ref.year - birth_date.year - ((ref.month, ref.day) < (birth_date.month, birth_date.day))


def _fmt_height_inches(height_inches: int | None) -> str:
    if height_inches is None:
        return "—"
    try:
        h = int(height_inches)
    except (TypeError, ValueError):
        return "—"
    if h <= 0:
        return "—"
    return f"{h // 12}'{h % 12}\""


def _shoots_label(raw: str | None) -> str:
    txt = (raw or "").strip().lower()
    if txt.startswith("l"):
        return "Left"
    if txt.startswith("r"):
        return "Right"
    return (raw or "—").strip() or "—"


def build_player_snapshot_card(
    player: Player,
    *,
    team_abbr: str,
    age_ref: date | None = None,
) -> dict[str, Any]:
    """Card payload for ``macros/player_snapshot_card.html``."""
    if age_ref is None:
        age_ref = season_age_reference_date(None)
    rr = get_player_ratings_row(player.fhm_player_id)
    is_goalie = player_is_goalie_position(player)
    if is_goalie:
        cat = goalie_category_averages(rr)
        attrs = {
            "goa": int(round(cat.get("goa"))) if cat.get("goa") is not None else None,
            "men": int(round(cat.get("men"))) if cat.get("men") is not None else None,
        }
    else:
        cat = skater_category_averages(rr)
        attrs = {
            "off": int(round(cat.get("off"))) if cat.get("off") is not None else None,
            "def": int(round(cat.get("def"))) if cat.get("def") is not None else None,
            "phy": int(round(cat.get("phy"))) if cat.get("phy") is not None else None,
            "men": int(round(cat.get("men"))) if cat.get("men") is not None else None,
        }
    return {
        "player": player,
        "is_goalie": is_goalie,
        "age": player_age_years(player.birth_date, age_ref),
        "shoots": _shoots_label(player.shoots_catches),
        "height": _fmt_height_inches(player.height_inches),
        "weight": int(player.weight_lbs) if player.weight_lbs is not None else None,
        "attrs": attrs,
        "team_abbr": team_abbr,
    }
