"""Player headshot files: canonical naming and case-insensitive static resolution."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Player

# Site-wide fallback when no player-specific file matches (``app/static/players/default.png``).
DEFAULT_PLAYER_HEADSHOT_REL = "players/default.png"

# dirname -> (mtime, lower_name -> actual_filename). Avoids O(n) iterdir per player lookup.
_players_dir_index_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _players_lower_basename_index(players_dir: Path) -> dict[str, str]:
    key = str(players_dir.resolve())
    try:
        mtime = players_dir.stat().st_mtime
    except OSError:
        return {}
    cached = _players_dir_index_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    idx = {f.name.lower(): f.name for f in players_dir.iterdir() if f.is_file()}
    _players_dir_index_cache[key] = (mtime, idx)
    return idx


def slug_name_part(name: str) -> str:
    """Lowercase, safe for filenames; spaces/hyphens become single underscores."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def canonical_player_headshot_basename(player: Player) -> str | None:
    """
    Expected filename: ``firstname_lastname_day_month_year.png`` (all lowercase).

    Date uses birth date: ``{day}_{month}_{year}`` (no zero-padding).
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
    ``firstname_lastname_day_month_year.png``. Matching is **case-insensitive** on disk.
    If nothing matches, uses ``players/default.png`` when present.
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

    seen: set[str] = set()
    for base in candidates:
        key = base.lower()
        if key in seen:
            continue
        seen.add(key)

        for d in search_dirs:
            if not d.is_dir():
                continue
            direct = d / base
            if direct.is_file():
                return f"{d.relative_to(root).as_posix()}/{direct.name}"

            idx = _players_lower_basename_index(d)
            real = idx.get(key)
            if real:
                return f"{d.relative_to(root).as_posix()}/{real}"

    for d in search_dirs:
        default_path = d / "default.png"
        if default_path.is_file():
            return f"{d.relative_to(root).as_posix()}/default.png"
    if (root / DEFAULT_PLAYER_HEADSHOT_REL).is_file():
        return DEFAULT_PLAYER_HEADSHOT_REL
    return None
