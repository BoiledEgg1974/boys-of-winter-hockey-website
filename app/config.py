import os
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
    LeagueEntry("league2", "BOWL Historical", "bowl_historical"),
    LeagueEntry("bow", "BOWL Fantasy", "bowl_fantasy"),
    LeagueEntry("league3", "BOWL Cap", "bowl_cap"),
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


# Default league for standalone scripts and single-process `create_app(Config)`.
_ENV_LEAGUE_SLUG = os.environ.get("LEAGUE_SLUG", "bow")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-bow-league-key-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / _ENV_LEAGUE_SLUG}.db",
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
    db_path = BASE_DIR / "instance" / f"{slug}.db"
    raw_path = BASE_DIR / "data" / "imports" / "raw" / entry.raw_import_dir

    headshots_rel = "players/fantasy" if slug == "bow" else "players/shared"
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
