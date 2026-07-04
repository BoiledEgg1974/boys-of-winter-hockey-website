"""Join Our League availability and email helpers."""
from __future__ import annotations

from pathlib import Path

from flask import current_app
from sqlalchemy import select

from app.mail_util import send_site_email
from app.models import Team

JOIN_AVAILABLE_TEAMS_DIR = "join_league"
JOIN_AVAILABLE_TEAMS_FILENAME = "available_teams.txt"
LEGACY_JOIN_AVAILABLE_TEAMS_FILENAME = "join_league_available_teams.txt"
WAITLIST_OPTION = "Waitlist"


def _join_league_slug() -> str:
    return str(current_app.config.get("LEAGUE_SLUG") or "").strip().lower() or "default"


def join_available_teams_path() -> Path:
    slug = _join_league_slug()
    return Path(current_app.instance_path) / JOIN_AVAILABLE_TEAMS_DIR / slug / JOIN_AVAILABLE_TEAMS_FILENAME


def _legacy_join_available_teams_path() -> Path:
    return Path(current_app.instance_path) / LEGACY_JOIN_AVAILABLE_TEAMS_FILENAME


def _migrate_legacy_join_teams_file_if_needed(target: Path) -> None:
    """Copy the old site-wide file into this league's path once (per-league settings)."""
    if target.is_file():
        return
    legacy = _legacy_join_available_teams_path()
    if not legacy.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        return


def normalize_team_option(name: str | None) -> str:
    return " ".join(str(name or "").strip().split())


def dedupe_team_options(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = normalize_team_option(raw)
        if not name or name.lower() == WAITLIST_OPTION.lower():
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def configured_join_team_options() -> tuple[list[str], bool]:
    """Return configured open teams and whether the admin file exists."""
    teams_file = join_available_teams_path()
    _migrate_legacy_join_teams_file_if_needed(teams_file)
    if not teams_file.is_file():
        return [], False
    try:
        rows = teams_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], True
    return dedupe_team_options(
        [line for line in rows if line.strip() and not line.strip().startswith("#")]
    ), True


def join_league_team_options() -> list[str]:
    """Public Join Our League select options; Waitlist is always available."""
    options, has_admin_file = configured_join_team_options()
    if not has_admin_file:
        if _join_league_slug() == "bowl-fantasy":
            options = ["Tokyo Katanas"]
    return [WAITLIST_OPTION, *dedupe_team_options(options)]


def _team_option_keys(team: Team) -> set[str]:
    keys = {
        normalize_team_option(getattr(team, "slug", "")).casefold(),
        normalize_team_option(getattr(team, "name", "")).casefold(),
        normalize_team_option(team.full_display_name()).casefold(),
    }
    return {k for k in keys if k}


def join_league_available_team_banner_rows(session) -> list[dict[str, object]]:
    """Open Join League teams mapped to current Team rows for public logo banners."""
    options = [opt for opt in join_league_team_options() if opt.casefold() != WAITLIST_OPTION.casefold()]
    if not options:
        return []

    teams = list(session.scalars(select(Team).order_by(Team.name)).all())
    team_by_key: dict[str, Team] = {}
    for team in teams:
        for key in _team_option_keys(team):
            team_by_key.setdefault(key, team)

    rows: list[dict[str, object]] = []
    for option in options:
        label = normalize_team_option(option)
        team = team_by_key.get(label.casefold())
        rows.append(
            {
                "label": team.full_display_name() if team else label,
                "slug": str(getattr(team, "slug", "") or label).strip(),
                "team": team,
            }
        )
    return rows


def save_join_team_options(open_team_names: list[str]) -> None:
    teams_file = join_available_teams_path()
    teams_file.parent.mkdir(parents=True, exist_ok=True)
    names = dedupe_team_options(open_team_names)
    body = "\n".join(names)
    if body:
        body += "\n"
    teams_file.write_text(body, encoding="utf-8")


def build_join_application_email_body(
    payload: dict[str, str], heard_from: list[str]
) -> str:
    body_lines = [
        f"League: {current_app.config.get('LEAGUE_DISPLAY_NAME', '')}",
        f"First Name: {payload['first_name']}",
        f"Last Name: {payload['last_name']}",
        f"Email: {payload['email']}",
        f"Age: {payload['age']}",
        f"Location: {payload['location']}",
        f"Discord: {payload['discord_status']}",
        f"Available Team: {payload['available_team']}",
        f"Heard About League From: {', '.join(heard_from)}",
        f"Other Leagues Active In: {payload['other_leagues_count']}",
        f"Favorite NHL Team: {payload['favorite_nhl_team']}",
        f"Favorite Player: {payload['favorite_player']}",
        "",
        "Experience Description:",
        payload["experience"],
        "",
        "Hockey Knowledge Description:",
        payload["knowledge"],
        "",
        "Team Building Style:",
        payload["team_building_style"],
    ]
    return "\n".join(body_lines)


def send_join_league_email(payload: dict[str, str], heard_from: list[str]) -> None:
    recipient = str(
        current_app.config.get("JOIN_LEAGUE_RECIPIENT", "keenovdecimanus@gmail.com")
    ).strip()
    subject = (
        f"[{current_app.config.get('LEAGUE_DISPLAY_NAME', 'League')}] "
        f"Join League Application - {payload['first_name']} {payload['last_name']}"
    )
    send_site_email(
        subject=subject,
        body=build_join_application_email_body(payload, heard_from),
        to_addrs=[recipient],
    )


def mail_settings_summary() -> dict[str, object]:
    from app.mail_util import smtp_config_diagnostics

    return smtp_config_diagnostics()
