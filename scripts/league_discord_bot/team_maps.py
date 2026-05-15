"""Per-league team_id -> (abbrev, custom emoji mention string).

Synced with BOWL-STATS-BOT team_maps.py — FHM team IDs, not site ``teams.id``.
"""

from __future__ import annotations

from typing import Dict, Tuple

TeamEntry = Tuple[str, str]

HISTORICAL_TEAMS: Dict[int, TeamEntry] = {
    0: ("MTL", "<:MTL:1358674505853046814>"),
    3: ("TOR", "<:TOR:1479530374395723895>"),
    5: ("BOS", "<:BOS:1296221296371306536>"),
    8: ("CHI", "<:CHI:1391961982235705436>"),
    9: ("DET", "<:DET:1290119897803915296>"),
    10: ("NYR", "<:NYR:1479530385737257124>"),
    118: ("LAK", "<:LAK:1469123020680597668>"),
    119: ("MIN", "<:MIN:1469123055761489941>"),
    120: ("OAK", "<:OAK:1469123074694840320>"),
    121: ("PHI", "<:PHI:1469123032009146589>"),
    122: ("PIT", "<:PIT:1495575341354455111>"),
    123: ("STL", "<:STL:1469123043769974915>"),
}

FANTASY_TEAMS: Dict[int, TeamEntry] = {
    0: ("WIC", "<:WIC:1236501118864068731>"),
    3: ("TOR", "<:TOR:1252375053144686633>"),
    5: ("HAM", "<:HAM:1453221895775453327>"),
    8: ("CHI", "<:CHI:1207501451966816306>"),
    9: ("HAL", "<:HAL:1341868533881241631>"),
    10: ("MON", "<:MON:1373118140996915255>"),
    11: ("KUN", "<:KUN:1463263903902334976>"),
    12: ("POR", "<:POR:1472096312324522109>"),
    14: ("LON", "<:LON:1458639710170644510>"),
    15: ("PIT", "<:PIT:1388761974443081868>"),
    16: ("VIC", "<:VIC:1420938447958311013>"),
    17: ("TOK", "<:TOK:1388762046220210246>"),
    18: ("SIX", "<:SIX:1373118421478281297>"),
    19: ("KEN", "<:KEN:1383317330339041290>"),
    20: ("MTL", "<:MTL:1405351657314979951>"),
    21: ("FLA", "<:FLA:1472096060842180649>"),
    22: ("BGK", "<:BGK:1373117910989668453>"),
    23: ("EDM", "<:EDM:1207502268354662410>"),
    24: ("TRL", "<:TRL:1277415927889264681>"),
    25: ("CAN", "<:CAN:1486766929254285383>"),
    26: ("FW", "<:FW:1490064700170571776>"),
    278: ("IND", "<:IND:1250473797685870602>"),
    279: ("ME", "<:ME:1472096110389493965>"),
    280: ("VCR", "<:VCR:1472096172859461697>"),
}

CAP_TEAMS: Dict[int, TeamEntry] = {
    0: ("MTL", "<:MTL:1333588537664213113>"),
    3: ("TOR", "<:TOR:1333588859958591579>"),
    5: ("BOS", "<:BOS:1429889226425634916>"),
    8: ("CHI", "<:CHI:1333588196826812506>"),
    9: ("DET", "<:DET:1333588258680078397>"),
    10: ("NYR", "<:NYR:1333588602042454016>"),
    11: ("LAK", "<:LAK:1485466999764156529>"),
    12: ("DAL", "<:DAL:1398788096346161203>"),
    14: ("PHI", "<:PHI:1333588627946471474>"),
    15: ("PIT", "<:PIT:1383318357176221696>"),
    16: ("STL", "<:STL:1485467024984379523>"),
    17: ("BUF", "<:BUF:1449556416963809330>"),
    18: ("VAN", "<:VAN:1468084057333170300>"),
    19: ("CGY", "<:CGY:1429889471754539142>"),
    20: ("NYI", "<:NYI:1468083975972196435>"),
    21: ("NJD", "<:NJD:1383318334254092318>"),
    22: ("WAS", "<:WSH:1429890537913188352>"),
    23: ("EDM", "<:EDM:1449591412264796242>"),
    24: ("CAR", "<:CAR:1468084009962573898>"),
    25: ("COL", "<:COL:1429889654601158747>"),
    26: ("PHX", "<:PHX:1449556427747364864>"),
    214: ("SJS", "<:SJS:1360231209678016562>"),
    216: ("OTT", "<:OTT:1377784222483484682>"),
    217: ("TBL", "<:TBL:1377784481615708160>"),
    220: ("ANA", "<:ANA:1398787454424842322>"),
    221: ("FLA", "<:FLA:1398787440684171518>"),
    224: ("NAS", "<:NSH:1470179048859767068>"),
    227: ("ATL", "<:ATL:1486767286772695171>"),
}


def _league_key(league_slug: str) -> str:
    slug = str(league_slug or "").strip().lower()
    if "historical" in slug:
        return "historical"
    if "cap" in slug:
        return "cap"
    return "fantasy"


def teams_for_league_slug(league_slug: str) -> Dict[int, TeamEntry]:
    key = _league_key(league_slug)
    if key == "historical":
        return HISTORICAL_TEAMS
    if key == "cap":
        return CAP_TEAMS
    return FANTASY_TEAMS


def emoji_for_abbrev(league_slug: str, abbrev: str) -> str:
    """First matching emoji for abbrev (league rosters are unique in practice)."""
    abbr = str(abbrev or "").strip().upper()
    if not abbr:
        return ""
    for _tid, (team_abbr, emoji) in teams_for_league_slug(league_slug).items():
        if team_abbr == abbr:
            return emoji
    return ""


def entry_for_fhm_team_id(league_slug: str, fhm_team_id: int | str | None) -> TeamEntry | None:
    if fhm_team_id is None:
        return None
    try:
        tid = int(fhm_team_id)
    except (TypeError, ValueError):
        return None
    return teams_for_league_slug(league_slug).get(tid)


def team_emoji_prefix(league_slug: str, payload: dict) -> str:
    """Leading custom emoji mention for a payload with fhm_team_id or team_abbrev."""
    entry = entry_for_fhm_team_id(league_slug, payload.get("fhm_team_id"))
    if entry:
        emoji = entry[1]
        return f"{emoji} " if emoji else ""
    abbrev = str(payload.get("team_abbrev") or "").strip()
    if abbrev:
        emoji = emoji_for_abbrev(league_slug, abbrev)
        return f"{emoji} " if emoji else ""
    return ""


def format_team_label(league_slug: str, payload: dict, *, fallback_name: str = "") -> str:
    """Emoji prefix + display name (abbrev from map when available)."""
    prefix = team_emoji_prefix(league_slug, payload)
    entry = entry_for_fhm_team_id(league_slug, payload.get("fhm_team_id"))
    name = str(fallback_name or payload.get("team_name") or "").strip()
    if entry:
        abbrev = entry[0]
        if name:
            return f"{prefix}**{abbrev}** — {name}".strip()
        return f"{prefix}**{abbrev}**".strip()
    if name:
        return f"{prefix}**{name}**".strip()
    return prefix.strip()
