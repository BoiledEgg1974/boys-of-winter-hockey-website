"""Player headshot files: canonical naming and punctuation-insensitive static resolution."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Player

# Site-wide fallback when no player-specific file matches (``app/static/players/default.png``).
DEFAULT_PLAYER_HEADSHOT_REL = "players/default.png"

_ASCII_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.]+$")

# dirname -> (mtime, lower_name -> actual, match_key -> actual). Avoids O(n) iterdir per lookup.
_players_dir_index_cache: dict[str, tuple[float, dict[str, str], dict[str, str]]] = {}


def headshot_match_key(name: str) -> str:
    """ASCII letters+digits only so case, hyphens, apostrophes, and accents do not matter."""
    raw = unicodedata.normalize("NFKD", Path(name or "").name)
    asciiish = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", asciiish.casefold())


def _prefer_headshot_filename(names: list[str]) -> str:
    """Prefer portable ASCII filenames when several files collapse to the same match key."""

    def sort_key(name: str) -> tuple[int, str]:
        portable = 0 if _ASCII_FILENAME_RE.fullmatch(name) else 1
        return (portable, name.casefold())

    return sorted(names, key=sort_key)[0]


def _players_basename_indexes(players_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    key = str(players_dir.resolve())
    try:
        mtime = players_dir.stat().st_mtime
    except OSError:
        return {}, {}
    cached = _players_dir_index_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    lower_idx: dict[str, str] = {}
    fuzzy_groups: dict[str, list[str]] = {}
    for f in players_dir.iterdir():
        if not f.is_file():
            continue
        lower_idx[f.name.lower()] = f.name
        match_key = headshot_match_key(f.name)
        if match_key:
            fuzzy_groups.setdefault(match_key, []).append(f.name)
    fuzzy_idx = {mk: _prefer_headshot_filename(names) for mk, names in fuzzy_groups.items()}
    _players_dir_index_cache[key] = (mtime, lower_idx, fuzzy_idx)
    return lower_idx, fuzzy_idx


def slug_name_part(name: str) -> str:
    """Lowercase ASCII; spaces, hyphens, apostrophes, and similar punctuation become underscores."""
    raw = unicodedata.normalize("NFKD", (name or "").strip())
    s = "".join(ch for ch in raw if not unicodedata.combining(ch)).casefold()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def canonical_player_headshot_basename(player: Player) -> str | None:
    """
    Expected filename: ``firstname_lastname_day_month_year.png`` (all lowercase ASCII).

    Date uses birth date: ``{day}_{month}_{year}`` (no zero-padding). Hyphens, apostrophes,
    periods, and accents are ignored so the same player resolves on every league site.
    """
    if player.birth_date is None:
        return None
    fn = slug_name_part(player.first_name)
    ln = slug_name_part(player.last_name)
    if not fn or not ln:
        return None
    d = player.birth_date
    return f"{fn}_{ln}_{d.day}_{d.month}_{d.year}.png"


def _basename_from_headshot_field(headshot_path: str) -> str:
    fn = headshot_path.strip().replace("\\", "/")
    return fn.split("/")[-1] if fn else ""


def resolve_player_headshot_static_filename(
    static_folder: str | Path,
    player: Player | None,
    players_rel_dir: str = "players",
) -> str | None:
    """
    Return static-relative path ``<players_rel_dir>/<file>`` for ``url_for('static', ...)``.

    Tries, in order: ``headshot_path`` from the DB (if set), then the canonical
    ``firstname_lastname_day_month_year.png``. Matching ignores case, hyphens, apostrophes,
    periods, and diacritics on disk. If nothing matches, uses ``players/default.png`` when present.
    """
    if not player:
        return None
    root = Path(static_folder)
    rel_dir = (players_rel_dir or "players").strip("/\\")
    primary_dir = root / rel_dir
    fallback_dir = root / "players"
    search_dirs = [primary_dir]
    # Backward compatibility for existing flat app/static/players uploads.
    if fallback_dir != primary_dir:
        search_dirs.append(fallback_dir)

    candidates: list[str] = []
    if player.headshot_path:
        base = _basename_from_headshot_field(player.headshot_path)
        if base:
            candidates.append(base)
    canon = canonical_player_headshot_basename(player)
    if canon:
        candidates.append(canon)

    seen_lower: set[str] = set()
    seen_fuzzy: set[str] = set()
    for base in candidates:
        key = base.lower()
        fuzzy = headshot_match_key(base)
        if key in seen_lower and (not fuzzy or fuzzy in seen_fuzzy):
            continue
        seen_lower.add(key)
        if fuzzy:
            seen_fuzzy.add(fuzzy)

        for d in search_dirs:
            if not d.is_dir():
                continue
            # Resolve through the directory index so Windows case-insensitive
            # hits still return the real on-disk name used by Linux/nginx.
            lower_idx, fuzzy_idx = _players_basename_indexes(d)
            real = lower_idx.get(key)
            if not real and fuzzy:
                real = fuzzy_idx.get(fuzzy)
            if real:
                return f"{d.relative_to(root).as_posix()}/{real}"

    for d in search_dirs:
        default_path = d / "default.png"
        if default_path.is_file():
            return f"{d.relative_to(root).as_posix()}/default.png"
    if (root / DEFAULT_PLAYER_HEADSHOT_REL).is_file():
        return DEFAULT_PLAYER_HEADSHOT_REL
    return None
