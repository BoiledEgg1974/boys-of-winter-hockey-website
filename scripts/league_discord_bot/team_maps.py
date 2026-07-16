"""Per-league team_id -> (abbrev, custom emoji mention string).

Synced with BOWL-STATS-BOT team_maps.py — FHM team IDs, not site ``teams.id``.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

TeamEntry = Tuple[str, str]

# League logo custom emotes (one per BOWL mount). Leave blank until configured on your server.
LEAGUE_LOGO_EMOJIS: Dict[str, str] = {
    "historical": "<:BOWLH:1231984450733211698>",
    "fantasy": "<:BOWL:1221309791465635851>",
    "cap": "<:BOWL:1333588086092992564>",
}

# Custom export status emotes. Leave blank to use Unicode checkmark/cross in the embed.
EXPORT_STATUS_EMOJIS: Dict[str, str] = {
    "success": "<:YES:1522716684060983437>",
    "fail": "<:NO:1522716685516279890>",
}

_CUSTOM_EMOJI_MENTION_RE = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")

HISTORICAL_TEAMS: Dict[int, TeamEntry] = {
    0: ("MTL", "<:MTL:1358674505853046814>"),
    3: ("TOR", "<:TOR:1527136626579476610>"),
    5: ("BOS", "<:BOS:1296221296371306536>"),
    8: ("CHI", "<:CHI:1391961982235705436>"),
    9: ("DET", "<:DET:1290119897803915296>"),
    10: ("NYR", "<:NYR:1479530385737257124>"),
    118: ("LAK", "<:LAK:1469123020680597668>"),
    119: ("MIN", "<:MIN:1469123055761489941>"),
    120: ("CAL", "<:CAL:1527136600721719487>"),
    121: ("PHI", "<:PHI:1469123032009146589>"),
    122: ("PIT", "<:PIT:1495575341354455111>"),
    123: ("STL", "<:STL:1469123043769974915>"),
    130: ("BUF", "<:BUF:1523803268244049930>"),
    131: ("VAN", "<:VAN:1523803266784301056>")
}

FANTASY_TEAMS: Dict[int, TeamEntry] = {
    0: ("WIC", "<:WIC:1236501118864068731>"),
    3: ("TOR", "<:TOR:1252375053144686633>"),
    5: ("HAM", "<:HAM:1453221895775453327>"),
    8: ("CHI", "<:CHI:1207501451966816306>"),
    9: ("HAL", "<:HAL:1341868533881241631>"),
    10: ("MON", "<:MON:1373118140996915255>"),
    11: ("KUN", "<:KUN:1463263903902334976>"),
    12: ("HEL", "<:HEL:1503501660490563656>"),
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
    10: ("NYR", "<:NYR:1522040487597314329>"),
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
    229: ("CBJ", "<:CBJ:1521962010865303592>"),
    230: ("MIN", "<:MIN:1521962022227546172>"),
}


def _league_key(league_slug: str) -> str:
    slug = str(league_slug or "").strip().lower()
    if "historical" in slug:
        return "historical"
    if "cap" in slug:
        return "cap"
    return "fantasy"


# Discord embed sidebar colors for closed sim-cycle boards (aligned with site.css league accents).
SIM_CYCLE_EMBED_COLORS: Dict[str, int] = {
    "historical": 0x166534,  # green
    "cap": 0xB91C1C,  # red
    "fantasy": 0x005DA6,  # blue (relegation)
}


def sim_cycle_embed_color(league_slug: str) -> int:
    return SIM_CYCLE_EMBED_COLORS.get(_league_key(league_slug), 0x166534)


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


def _emote_snowflake(mention: str) -> str | None:
    m = _CUSTOM_EMOJI_MENTION_RE.fullmatch(str(mention or "").strip())
    if not m:
        return None
    return str(m.group(2))


def _team_emote_snowflakes(league_slug: str) -> set[str]:
    out: set[str] = set()
    for _tid, (_abbr, emoji) in teams_for_league_slug(league_slug).items():
        snowflake = _emote_snowflake(emoji)
        if snowflake:
            out.add(snowflake)
    return out


def _safe_custom_emote(mention: str, *, league_slug: str) -> str:
    """Return custom emote only when it does not collide with a team logo on this league."""
    text = str(mention or "").strip()
    if not text:
        return ""
    snowflake = _emote_snowflake(text)
    if snowflake and snowflake in _team_emote_snowflakes(league_slug):
        return ""
    return text


def league_logo_emoji(league_slug: str, *, prefer_league_brand: bool = False) -> str:
    """Return the league brand custom emote.

    When *prefer_league_brand* is True (league-wide news), always use the configured
    league logo even if its snowflake overlaps a team logo entry.
    """
    key = _league_key(league_slug)
    raw = str(LEAGUE_LOGO_EMOJIS.get(key) or "").strip()
    if prefer_league_brand:
        return raw
    return _safe_custom_emote(raw, league_slug=league_slug)


def export_status_emoji(*, success: bool, league_slug: str = "") -> str:
    key = "success" if success else "fail"
    raw = str(EXPORT_STATUS_EMOJIS.get(key) or "").strip()
    if not raw:
        return ""
    if league_slug:
        return _safe_custom_emote(raw, league_slug=league_slug)
    return raw


def fhm_team_id_for_abbrev(league_slug: str, abbrev: str) -> int | None:
    abbr = str(abbrev or "").strip().upper()
    if not abbr:
        return None
    for tid, (team_abbr, _emoji) in teams_for_league_slug(league_slug).items():
        if team_abbr == abbr:
            return int(tid)
    return None


def fhm_team_id_for_custom_emoji_mention(league_slug: str, mention: str) -> int | None:
    text = str(mention or "").strip()
    if not text:
        return None
    m = _CUSTOM_EMOJI_MENTION_RE.fullmatch(text)
    if not m:
        return None
    _name, snowflake = m.group(1), m.group(2)
    for tid, (_abbr, emoji) in teams_for_league_slug(league_slug).items():
        em = _CUSTOM_EMOJI_MENTION_RE.fullmatch(str(emoji or "").strip())
        if em and em.group(2) == snowflake:
            return int(tid)
    return None


def fhm_team_id_from_message_token(league_slug: str, token: str) -> int | None:
    text = str(token or "").strip()
    if not text:
        return None
    if text.startswith("<"):
        tid = fhm_team_id_for_custom_emoji_mention(league_slug, text)
        if tid is not None:
            return tid
    if text.isdigit():
        try:
            tid = int(text)
        except ValueError:
            return None
        if tid in teams_for_league_slug(league_slug):
            return tid
    return fhm_team_id_for_abbrev(league_slug, text)


def format_team_label(league_slug: str, payload: dict, *, fallback_name: str = "") -> str:
    """Emoji prefix + display name (abbrev from map when available)."""
    if payload.get("league_wide"):
        logo = league_logo_emoji(league_slug, prefer_league_brand=True)
        prefix = f"{logo} " if logo else ""
        name = str(fallback_name or payload.get("team_name") or "League").strip()
        return f"{prefix}**{name}**".strip() if name else prefix.strip()

    prefix = team_emoji_prefix(league_slug, payload)
    entry = entry_for_fhm_team_id(league_slug, payload.get("fhm_team_id"))
    name = str(fallback_name or payload.get("team_name") or "").strip()
    team_url = str(payload.get("team_url") or "").strip()

    def link(label: str) -> str:
        return f"[{label}]({team_url})" if team_url else label

    if entry:
        abbrev = entry[0]
        if name:
            return f"{prefix}**{link(abbrev)}** — {link(name)}".strip()
        return f"{prefix}**{link(abbrev)}**".strip()
    if name:
        return f"{prefix}**{link(name)}**".strip()
    return prefix.strip()
