"""Group averages for ``player_ratings.csv`` rows (skater OFF/DEF/PHY/MEN, goalie GOA/MEN).

All values are derived from the normalized CSV row keyed by FHM ``PlayerId`` (see
``get_player_ratings_row``). There is no separate database table for these averages.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Keys must match normalized headers from player_ratings.csv
# Aggregate “overview” skills (Skating, Shooting, …) — distinct from per-attribute offense/defense rows.
OVERVIEW_KEYS = (
    "skating",
    "shooting",
    "playmaking",
    "defending",
    "physicality",
    "conditioning",
    "character",
    "hockey_sense",
)
OFF_KEYS = (
    "screening",
    "getting_open",
    "passing",
    "puck_handling",
    "shooting_accuracy",
    "shooting_range",
    "offensive_read",
)
DEF_KEYS = (
    "checking",
    "faceoffs",
    "hitting",
    "positioning",
    "shot_blocking",
    "stickchecking",
    "defensive_read",
)
MENTAL_KEYS = (
    "aggression",
    "bravery",
    "determination",
    "teamplayer",
    "leadership",
    "temperament",
    "professionalism",
    "mental_toughness",
)
# Skater Mental panel (matches player.html): same as MENTAL_KEYS but no mental_toughness
MENTAL_KEYS_SKATER = (
    "aggression",
    "bravery",
    "determination",
    "teamplayer",
    "leadership",
    "temperament",
    "professionalism",
)
# Goalie profile: omit bravery & temperament; Stamina uses CSV ``goalie_stamina`` in Mental panel
MENTAL_KEYS_GOALIE = (
    "aggression",
    "mental_toughness",
    "determination",
    "teamplayer",
    "leadership",
    "goalie_stamina",
    "professionalism",
)
# First 11 goalie skills (matches reference “Goalie ratings”); technique/overall/stamina excluded here
GOALIE_KEYS_GOA = (
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
PHYS_KEYS = (
    "acceleration",
    "agility",
    "balance",
    "speed",
    "stamina",
    "strength",
    "fighting",
)
def _float_cell(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def average_for_keys(row: dict | None, keys: tuple[str, ...]) -> float | None:
    if not row:
        return None
    vals: list[float] = []
    for k in keys:
        f = _float_cell(row.get(k))
        if f is not None:
            vals.append(f)
    if not vals:
        return None
    return sum(vals) / len(vals)


def skater_category_averages(row: dict | None) -> dict[str, float | None]:
    if not row:
        return {"off": None, "def": None, "phy": None, "men": None}
    return {
        "off": average_for_keys(row, OFF_KEYS),
        "def": average_for_keys(row, DEF_KEYS),
        "phy": average_for_keys(row, PHYS_KEYS),
        "men": average_for_keys(row, MENTAL_KEYS_SKATER),
    }


def goalie_category_averages(row: dict | None) -> dict[str, float | None]:
    if not row:
        return {"goa": None, "men": None}
    return {
        "goa": average_for_keys(row, GOALIE_KEYS_GOA),
        "men": average_for_keys(row, MENTAL_KEYS_GOALIE),
    }


# English / common hockey export names → ISO 3166-1 alpha-2 (lowercase for flagcdn)
_COUNTRY_TO_ISO2: dict[str, str] = {
    "afghanistan": "af",
    "albania": "al",
    "argentina": "ar",
    "armenia": "am",
    "australia": "au",
    "austria": "at",
    "azerbaijan": "az",
    "belarus": "by",
    "belgium": "be",
    "bosnia and herzegovina": "ba",
    "brazil": "br",
    "bulgaria": "bg",
    "canada": "ca",
    "china": "cn",
    "croatia": "hr",
    "czech republic": "cz",
    "czechia": "cz",
    "denmark": "dk",
    "estonia": "ee",
    "finland": "fi",
    "france": "fr",
    "georgia": "ge",
    "germany": "de",
    "greece": "gr",
    "hungary": "hu",
    "iceland": "is",
    "ireland": "ie",
    "italy": "it",
    "japan": "jp",
    "kazakhstan": "kz",
    "latvia": "lv",
    "lithuania": "lt",
    "luxembourg": "lu",
    "mexico": "mx",
    "netherlands": "nl",
    "norway": "no",
    "poland": "pl",
    "romania": "ro",
    "russia": "ru",
    "serbia": "rs",
    "slovakia": "sk",
    "slovenia": "si",
    "south korea": "kr",
    "korea": "kr",
    "republic of korea": "kr",
    "spain": "es",
    "sweden": "se",
    "switzerland": "ch",
    "taiwan": "tw",
    "thailand": "th",
    "ukraine": "ua",
    "united kingdom": "gb",
    "great britain": "gb",
    "england": "gb",
    "scotland": "gb",
    "wales": "gb",
    "northern ireland": "gb",
    "the united states": "us",
    "the united states of america": "us",
    "united states": "us",
    "usa": "us",
    "u.s.a.": "us",
    "u.s.a": "us",
    "vietnam": "vn",
}


def nationality_to_iso2(nationality: str | None) -> str | None:
    if not nationality:
        return None
    key = nationality.strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in _COUNTRY_TO_ISO2:
        return _COUNTRY_TO_ISO2[key]
    # Try without punctuation
    key2 = re.sub(r"[^a-z\s]", "", key)
    key2 = re.sub(r"\s+", " ", key2).strip()
    return _COUNTRY_TO_ISO2.get(key2)


def flag_icon_url(nationality: str | None, width: int = 20) -> str | None:
    """HTTPS flagcdn.com URL for a small flag icon, or None if unknown."""
    iso = nationality_to_iso2(nationality)
    if not iso:
        return None
    return f"https://flagcdn.com/w{width}/{iso}.png"
