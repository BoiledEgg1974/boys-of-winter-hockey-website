"""Per-league FHM team_id -> (abbrev, custom emoji mention string).

Synced with BOWL-STATS-BOT team_maps.py — FHM team IDs, not site ``teams.id``.

Maintenance
-----------
Edit only the ``team(...)`` rows in each ``_*_SPECS`` map below. Lookups by
FHM id, abbrev, historical alias (e.g. OAK→CAL), and Discord snowflake are
built automatically — no other files need emote ID updates.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, NamedTuple, Tuple

TeamEntry = Tuple[str, str]


class TeamSpec(NamedTuple):
    """One Discord team logo mapping.

    *abbrev* is the primary label shown in posts (usually matches the custom
    emoji name). *emoji_id* is the Discord snowflake only — leave blank if the
    server does not have an emote yet. *aliases* are alternate abbrevs (site or
    historical names) that resolve to the same FHM id / emote. *emoji_name*
    overrides the ``<:Name:id>`` name when it differs from *abbrev* (e.g. WAS→WSH).
    """

    abbrev: str
    emoji_id: str = ""
    aliases: Tuple[str, ...] = ()
    emoji_name: str | None = None


def team(
    abbrev: str,
    emoji_id: str | int = "",
    *aliases: str,
    emoji_name: str | None = None,
) -> TeamSpec:
    """Declare a team emote row for a league map."""
    abbr = str(abbrev or "").strip().upper()
    eid = str(emoji_id or "").strip()
    alias_tuple = tuple(str(a).strip().upper() for a in aliases if str(a).strip())
    name = str(emoji_name).strip().upper() if emoji_name else None
    return TeamSpec(abbrev=abbr, emoji_id=eid, aliases=alias_tuple, emoji_name=name)


def _mention_for(spec: TeamSpec) -> str:
    if not spec.emoji_id:
        return ""
    name = spec.emoji_name or spec.abbrev
    return f"<:{name}:{spec.emoji_id}>"


def _compile_team_map(
    specs: Mapping[int, TeamSpec],
) -> tuple[Dict[int, TeamEntry], Dict[str, int]]:
    teams: Dict[int, TeamEntry] = {}
    abbrev_to_fhm: Dict[str, int] = {}
    for tid, spec in specs.items():
        teams[int(tid)] = (spec.abbrev, _mention_for(spec))
        labels = (spec.abbrev, *spec.aliases)
        for label in labels:
            if not label:
                continue
            existing = abbrev_to_fhm.get(label)
            if existing is not None and existing != int(tid):
                raise ValueError(
                    f"Duplicate team abbrev {label!r} for FHM ids {existing} and {tid}"
                )
            abbrev_to_fhm[label] = int(tid)
    return teams, abbrev_to_fhm


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

# --- Edit team rows here (abbrev, Discord emoji snowflake, optional aliases) ---

_HISTORICAL_SPECS: Dict[int, TeamSpec] = {
    0: team("MTL", "1358674505853046814"),
    3: team("TOR", "1527136626579476610"),
    5: team("BOS", "1296221296371306536"),
    8: team("CHI", "1391961982235705436"),
    9: team("DET", "1290119897803915296"),
    10: team("NYR", "1543797351443857448"),
    118: team("LAK", "1469123020680597668"),
    119: team("MIN", "1469123055761489941"),
    # FHM/Discord use CAL (California); site roster still uses OAK (Oakland).
    120: team("CAL", "1527136600721719487", "OAK"),
    121: team("PHI", "1469123032009146589"),
    122: team("PIT", "1495575341354455111"),
    123: team("STL", "1469123043769974915"),
    130: team("BUF", "1523803268244049930"),
    131: team("VAN", "1523803266784301056"),
}

_FANTASY_SPECS: Dict[int, TeamSpec] = {
    0: team("WIC", "1236501118864068731"),
    3: team("TOR", "1252375053144686633"),
    5: team("HAM", "1453221895775453327"),
    8: team("CHI", "1207501451966816306"),
    9: team("HAL", "1341868533881241631"),
    10: team("MON", "1373118140996915255"),
    11: team("KUN", "1463263903902334976"),
    12: team("HEL", "1503501660490563656"),
    14: team("LON", "1458639710170644510"),
    15: team("PIT", "1388761974443081868"),
    16: team("VIC", "1420938447958311013"),
    17: team("TOK", "1388762046220210246"),
    18: team("SIX", "1373118421478281297"),
    19: team("KEN", "1383317330339041290"),
    20: team("MTL", "1405351657314979951"),
    21: team("FLA", "1472096060842180649"),
    22: team("BGK", "1373117910989668453"),
    23: team("EDM", "1207502268354662410"),
    24: team("TRL", "1277415927889264681"),
    25: team("CAN", "1486766929254285383"),
    26: team("FW", "1490064700170571776"),
    278: team("IND", "1250473797685870602"),
    279: team("ME", "1472096110389493965"),
    280: team("VCR", "1472096172859461697"),
}

_CAP_SPECS: Dict[int, TeamSpec] = {
    0: team("MTL", "1333588537664213113"),
    3: team("TOR", "1333588859958591579"),
    5: team("BOS", "1429889226425634916"),
    8: team("CHI", "1333588196826812506"),
    9: team("DET", "1333588258680078397"),
    10: team("NYR", "1522040487597314329"),
    11: team("LAK", "1485466999764156529"),
    12: team("DAL", "1398788096346161203"),
    14: team("PHI", "1333588627946471474"),
    15: team("PIT", "1383318357176221696"),
    16: team("STL", "1485467024984379523"),
    17: team("BUF", "1449556416963809330"),
    18: team("VAN", "1468084057333170300"),
    19: team("CGY", "1429889471754539142"),
    20: team("NYI", "1468083975972196435"),
    21: team("NJD", "1383318334254092318"),
    22: team("WAS", "1429890537913188352", emoji_name="WSH"),
    23: team("EDM", "1449591412264796242"),
    24: team("CAR", "1468084009962573898"),
    25: team("COL", "1429889654601158747"),
    26: team("PHX", "1449556427747364864"),
    214: team("SJS", "1360231209678016562"),
    216: team("OTT", "1377784222483484682"),
    217: team("TBL", "1377784481615708160"),
    220: team("ANA", "1398787454424842322"),
    221: team("FLA", "1398787440684171518"),
    224: team("NAS", "1470179048859767068", emoji_name="NSH"),
    227: team("ATL", "1486767286772695171"),
    229: team("CBJ", "1521962010865303592"),
    230: team("MIN", "1521962022227546172"),
}

HISTORICAL_TEAMS, _HISTORICAL_ABBREV_INDEX = _compile_team_map(_HISTORICAL_SPECS)
FANTASY_TEAMS, _FANTASY_ABBREV_INDEX = _compile_team_map(_FANTASY_SPECS)
CAP_TEAMS, _CAP_ABBREV_INDEX = _compile_team_map(_CAP_SPECS)


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


def _abbrev_index_for_league(league_slug: str) -> Dict[str, int]:
    key = _league_key(league_slug)
    if key == "historical":
        return _HISTORICAL_ABBREV_INDEX
    if key == "cap":
        return _CAP_ABBREV_INDEX
    return _FANTASY_ABBREV_INDEX


def emoji_for_abbrev(league_slug: str, abbrev: str) -> str:
    """Emoji for abbrev or alias (league rosters are unique in practice)."""
    abbr = str(abbrev or "").strip().upper()
    if not abbr:
        return ""
    tid = _abbrev_index_for_league(league_slug).get(abbr)
    if tid is None:
        return ""
    entry = teams_for_league_slug(league_slug).get(tid)
    return entry[1] if entry else ""


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
    return _abbrev_index_for_league(league_slug).get(abbr)


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
