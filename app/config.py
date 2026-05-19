import os
import sqlite3
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


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


def undrafted_prospects_max_age(league_slug: str) -> int:
    """Inclusive upper age for the Undrafted Prospects list (and matching Free Agents exclusion)."""
    if league_slug in ("bowl-fantasy", "bowl-cap"):
        return 18
    return 20


def undrafted_prospects_age_filter_options(league_slug: str) -> tuple[int, ...]:
    """Exact-age filter values (high to low) for the Undrafted Prospects form."""
    top = undrafted_prospects_max_age(league_slug)
    return tuple(range(top, 14, -1))


def free_agents_exclude_nhl_bowl_drafted_max_age(league_slug: str) -> int | None:
    """Inclusive upper age for hiding NHL/BOWL draft picks from Free Agents.

    Saves with full draft imports list juniors on other leagues without contract/prospect rows; those
    players are not true UFAs. Historical uses ``None`` so sparse draft data does not empty the list.
    """
    if league_slug in ("bowl-fantasy", "bowl-cap"):
        return 27
    return None


def rookie_homepage_thresholds(league_slug: str) -> dict[str, float | int]:
    """Homepage rookie-board display thresholds.

    Keep this league-specific map in code so each site can tune rookie visibility independently.
    RS values are blended: ``max(abs_min, pct_of_league_schedule)``.
    """
    defaults: dict[str, float | int] = {
        "rs_skater_min_gp_abs": 10,
        "rs_skater_min_gp_pct": 0.20,
        "rs_goalie_min_minutes_abs": 600,
        "rs_goalie_min_minutes_pct": 0.20,
        "pspo_skater_min_gp": 2,
        "pspo_goalie_min_minutes": 120,
    }
    per_league: dict[str, dict[str, float | int]] = {
        "bowl-historical": {},
        "bowl-fantasy": {},
        "bowl-cap": {},
    }
    out = dict(defaults)
    out.update(per_league.get(league_slug, {}))
    return out


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


def resolve_site_sqlite_path() -> Path:
    """Shared membership / AP / news database (all league mounts + hub)."""
    inst = BASE_DIR / "instance"
    inst.mkdir(parents=True, exist_ok=True)
    return (inst / "site_membership.db").resolve()


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
    # SQLite: longer busy wait + thread-safe pool use (see ``sqlite_pragmas`` for WAL).
    _SQLITE_BUSY_SECONDS = float(os.environ.get("SQLITE_BUSY_TIMEOUT_SECONDS", "30"))
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "timeout": _SQLITE_BUSY_SECONDS,
            "check_same_thread": False,
        },
    }
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
    _ROOKIE = rookie_homepage_thresholds(_ENV_LEAGUE_SLUG)
    ROOKIE_RS_SKATER_MIN_GP_ABS = int(_ROOKIE["rs_skater_min_gp_abs"])
    ROOKIE_RS_SKATER_MIN_GP_PCT = float(_ROOKIE["rs_skater_min_gp_pct"])
    ROOKIE_RS_GOALIE_MIN_MINUTES_ABS = int(_ROOKIE["rs_goalie_min_minutes_abs"])
    ROOKIE_RS_GOALIE_MIN_MINUTES_PCT = float(_ROOKIE["rs_goalie_min_minutes_pct"])
    ROOKIE_PSPO_SKATER_MIN_GP = int(_ROOKIE["pspo_skater_min_gp"])
    ROOKIE_PSPO_GOALIE_MIN_MINUTES = int(_ROOKIE["pspo_goalie_min_minutes"])
    _CACHE_WARM_RAW = os.environ.get("LEAGUE_JSON_CACHE_WARM_ON_STARTUP", "1").strip().lower()
    LEAGUE_JSON_CACHE_WARM_ON_STARTUP = _CACHE_WARM_RAW in ("1", "true", "yes", "on")
    HOMEPAGE_POSTSEASON_MC_SIMS = int(os.environ.get("HOMEPAGE_POSTSEASON_MC_SIMS", "600") or 600)
    JOIN_LEAGUE_RECIPIENT = os.environ.get("JOIN_LEAGUE_RECIPIENT", "keenovdecimanus@gmail.com")
    # Optional comma-separated extra inboxes for admin review alerts (news, AP, memberships).
    ADMIN_ALERT_EMAILS = os.environ.get("ADMIN_ALERT_EMAILS", "")
    MAIL_SMTP_HOST = os.environ.get("MAIL_SMTP_HOST", "")
    MAIL_SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "587"))
    MAIL_SMTP_USERNAME = os.environ.get("MAIL_SMTP_USERNAME", "")
    MAIL_SMTP_PASSWORD = os.environ.get("MAIL_SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", MAIL_SMTP_USERNAME or JOIN_LEAGUE_RECIPIENT)
    MAIL_SMTP_USE_TLS = os.environ.get("MAIL_SMTP_USE_TLS", "1").lower() not in {"0", "false", "no", "off"}
    MAIL_SMTP_USE_SSL = os.environ.get("MAIL_SMTP_USE_SSL", "0").lower() in {"1", "true", "yes", "on"}
    PASSWORD_RESET_TOKEN_TTL_MINUTES = int(os.environ.get("PASSWORD_RESET_TOKEN_TTL_MINUTES", "60"))
    SITE_SQLALCHEMY_DATABASE_URI = os.environ.get(
        "SITE_DATABASE_URL",
        f"sqlite:///{resolve_site_sqlite_path()}",
    )
    # GM news → AP when article is published (set later via env or admin UI constant)
    NEWS_ARTICLE_AP_POINTS = int(os.environ.get("NEWS_ARTICLE_AP_POINTS", "3"))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30"))
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
    WTF_CSRF_TIME_LIMIT = None
    # Initial password for auto-created commissioner user (override in production).
    COMMISH_ADMIN_PASSWORD = os.environ.get("COMMISH_ADMIN_PASSWORD", "Claudette81!")
    # Optional absolute site URL for story automation / Discord links (no trailing slash), e.g. https://bowl.example.com
    SITE_PUBLIC_BASE_URL = os.environ.get("SITE_PUBLIC_BASE_URL", "").rstrip("/")
    # Optional Discord incoming webhook URL for scheduled story posts (channel=discord).
    DISCORD_STORY_WEBHOOK_URL = os.environ.get("DISCORD_STORY_WEBHOOK_URL", "").strip()
    # Shared secret for bot pull/ack API endpoints (override in production via env).
    DISCORD_EVENTS_SHARED_SECRET = os.environ.get("DISCORD_EVENTS_SHARED_SECRET", "bowluniverse").strip()
    # Unified delivery bot (scripts/league_discord_bot); token is never stored in the database.
    DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    DISCORD_BOT_NAME = os.environ.get("DISCORD_BOT_NAME", "league-discord-bot").strip()[:120]
    DISCORD_BOT_VERSION = os.environ.get("DISCORD_BOT_VERSION", "1.0.0").strip()[:64]
    DISCORD_BOT_POLL_SECONDS = float(os.environ.get("DISCORD_BOT_POLL_SECONDS", "8"))
    # Comma-separated slug:base_url pairs, e.g. bowl-historical:https://www.bowlhockey.com/bowl-historical
    DISCORD_BOT_LEAGUE_BASE_URLS = os.environ.get("DISCORD_BOT_LEAGUE_BASE_URLS", "").strip()
    # GM AI Trade Tool (entertainment): OpenAI-compatible Chat Completions API key and model.
    TRADE_AI_OPENAI_API_KEY = (
        os.environ.get("TRADE_AI_OPENAI_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    TRADE_AI_OPENAI_MODEL = os.environ.get("TRADE_AI_OPENAI_MODEL", "gpt-4o-mini").strip()
    # When true, Draft Hub "AI" desk uses local heuristics only (no OpenAI), even if TRADE_AI_OPENAI_API_KEY is set.
    _DRAFT_HUB_AI_HEURISTIC_ONLY_RAW = os.environ.get("DRAFT_HUB_AI_HEURISTIC_ONLY", "").strip().lower()
    DRAFT_HUB_AI_HEURISTIC_ONLY = _DRAFT_HUB_AI_HEURISTIC_ONLY_RAW in {"1", "true", "yes", "on"}


def league_group_for_slug(slug: str) -> str:
    """Redemption catalog group: Fantasy vs Cap+Historical."""
    return "fantasy" if slug == "bowl-fantasy" else "cap_historical"


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
        APPLICATION_ROOT = f"/{slug}"
        LEAGUE_DISPLAY_NAME = entry.display_name
        TEAM_LOGOS_REL_DIR = team_logos_rel
        TEAM_LOGOS_DIR = BASE_DIR / "app" / "static" / team_logos_rel
        LEAGUE_LOGO_REL_DIR = league_logo_rel
        LEAGUE_LOGO_DIR = BASE_DIR / "app" / "static" / league_logo_rel
        HISTORY_CHAMPIONS_REL_DIR = champions_rel
        HISTORY_CHAMPIONS_DIR = BASE_DIR / "app" / "static" / champions_rel
        PLAYER_HEADSHOTS_REL_DIR = headshots_rel
        PLAYER_HEADSHOTS_DIR = BASE_DIR / "app" / "static" / headshots_rel
        _ROOKIE = rookie_homepage_thresholds(slug)
        ROOKIE_RS_SKATER_MIN_GP_ABS = int(_ROOKIE["rs_skater_min_gp_abs"])
        ROOKIE_RS_SKATER_MIN_GP_PCT = float(_ROOKIE["rs_skater_min_gp_pct"])
        ROOKIE_RS_GOALIE_MIN_MINUTES_ABS = int(_ROOKIE["rs_goalie_min_minutes_abs"])
        ROOKIE_RS_GOALIE_MIN_MINUTES_PCT = float(_ROOKIE["rs_goalie_min_minutes_pct"])
        ROOKIE_PSPO_SKATER_MIN_GP = int(_ROOKIE["pspo_skater_min_gp"])
        ROOKIE_PSPO_GOALIE_MIN_MINUTES = int(_ROOKIE["pspo_goalie_min_minutes"])

    return LeagueConfig
