import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class LeagueEntry:
    slug: str
    display_name: str
    raw_import_dir: str


# Single registry for hub splash, header switcher, and DispatcherMiddleware mounts.
LEAGUES: tuple[LeagueEntry, ...] = (
    LeagueEntry("bowl-historical", "BOWL-Historical", "bowl_historical"),
    LeagueEntry("bowl-fantasy", "BOWL-Fantasy", "bowl_fantasy"),
    LeagueEntry("bowl-cap", "BOWL-Cap", "bowl_cap"),
)


def league_slugs() -> list[str]:
    return [e.slug for e in LEAGUES]


def league_by_slug(slug: str) -> LeagueEntry | None:
    for e in LEAGUES:
        if e.slug == slug:
            return e
    return None


def league_display_name(slug: str) -> str:
    e = league_by_slug(slug)
    return e.display_name if e else slug


def league_raw_import_dir(slug: str) -> str:
    e = league_by_slug(slug)
    return e.raw_import_dir if e else slug


# If instance/<new-slug>.db is missing, use these pre-rename filenames (same DB content).
_LEGACY_LEAGUE_DB_FILES: dict[str, str] = {
    "bowl-historical": "league2.db",
    "bowl-fantasy": "bow.db",
    "bowl-cap": "league3.db",
}


def _sqlite_has_league_content(path: Path) -> bool:
    """True if the DB has at least one row in teams, players, or games (real import vs empty schema)."""
    try:
        ro_uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(ro_uri, uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(path))
        except sqlite3.Error:
            return False
    try:
        for table in ("teams", "players", "games", "seasons"):
            cur = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (table,),
            )
            if not cur.fetchone():
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n and int(n) > 0:
                return True
        return False
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def resolve_league_sqlite_path(slug: str) -> Path:
    """Pick SQLite file for this league.

    After a slug rename, empty ``instance/<slug>.db`` files are often created by ``db.create_all()`` while
    real data still lives in ``league2.db`` / ``bow.db`` / ``league3.db``. We prefer whichever file
    actually contains league rows; otherwise fall back to primary, then legacy, then primary for new installs.
    """
    inst = BASE_DIR / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    primary = inst / f"{slug}.db"
    legacy_name = _LEGACY_LEAGUE_DB_FILES.get(slug)
    legacy = inst / legacy_name if legacy_name else None

    prim_exists = primary.is_file()
    leg_exists = legacy.is_file() if legacy else False
    prim_populated = prim_exists and _sqlite_has_league_content(primary)
    leg_populated = leg_exists and legacy is not None and _sqlite_has_league_content(legacy)

    if prim_populated:
        return primary
    if leg_populated:
        return legacy
    # Empty bowl-*.db from db.create_all() must not win over an on-disk legacy file.
    if leg_exists and not prim_populated:
        return legacy
    if prim_exists:
        return primary
    if leg_exists:
        return legacy
    return primary


# Default league for standalone scripts and single-process `create_app(Config)`.
_ENV_LEAGUE_SLUG = os.environ.get("LEAGUE_SLUG", "bowl-fantasy")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-bow-league-key-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{resolve_league_sqlite_path(_ENV_LEAGUE_SLUG)}",
    )
    RAW_IMPORT_DIR = BASE_DIR / "data" / "imports" / "raw" / league_raw_import_dir(_ENV_LEAGUE_SLUG)
    TEAM_LOGOS_REL_DIR = "logos/teams"
    TEAM_LOGOS_DIR = BASE_DIR / "app" / "static" / TEAM_LOGOS_REL_DIR
    LEAGUE_LOGO_REL_DIR = "logos"
    LEAGUE_LOGO_DIR = BASE_DIR / "app" / "static" / LEAGUE_LOGO_REL_DIR
    HISTORY_CHAMPIONS_REL_DIR = "img/history/champions"
    HISTORY_CHAMPIONS_DIR = BASE_DIR / "app" / "static" / HISTORY_CHAMPIONS_REL_DIR
    PLAYER_HEADSHOTS_REL_DIR = "players"
    PLAYER_HEADSHOTS_DIR = BASE_DIR / "app" / "static" / PLAYER_HEADSHOTS_REL_DIR
    ITEMS_PER_PAGE = 50
    LEAGUE_SLUG = _ENV_LEAGUE_SLUG
    LEAGUE_DISPLAY_NAME = league_display_name(_ENV_LEAGUE_SLUG)


def make_league_config(slug: str) -> type:
    """Build a Config subclass for one mounted league app (fixed DB + CSV folder)."""
    entry = league_by_slug(slug)
    if entry is None:
        raise ValueError(f"Unknown league slug: {slug!r} (not in LEAGUES)")
    db_path = resolve_league_sqlite_path(slug)
    raw_path = BASE_DIR / "data" / "imports" / "raw" / entry.raw_import_dir

    headshots_rel = "players/fantasy" if slug == "bowl-fantasy" else "players/shared"
    team_logos_rel = f"logos/teams/{entry.raw_import_dir}"
    league_logo_rel = f"logos/{entry.raw_import_dir}"
    champions_rel = f"img/history/champions/{entry.raw_import_dir}"

    class LeagueConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        RAW_IMPORT_DIR = raw_path
        LEAGUE_SLUG = slug
        LEAGUE_DISPLAY_NAME = entry.display_name
        TEAM_LOGOS_REL_DIR = team_logos_rel
        TEAM_LOGOS_DIR = BASE_DIR / "app" / "static" / team_logos_rel
        LEAGUE_LOGO_REL_DIR = league_logo_rel
        LEAGUE_LOGO_DIR = BASE_DIR / "app" / "static" / league_logo_rel
        HISTORY_CHAMPIONS_REL_DIR = champions_rel
        HISTORY_CHAMPIONS_DIR = BASE_DIR / "app" / "static" / champions_rel
        PLAYER_HEADSHOTS_REL_DIR = headshots_rel
        PLAYER_HEADSHOTS_DIR = BASE_DIR / "app" / "static" / headshots_rel

    return LeagueConfig
