"""Join Our League availability and email helpers."""
from __future__ import annotations

from pathlib import Path

from flask import current_app

from app.mail_util import send_site_email

JOIN_AVAILABLE_TEAMS_FILENAME = "join_league_available_teams.txt"
WAITLIST_OPTION = "Waitlist"


def join_available_teams_path() -> Path:
    return Path(current_app.instance_path) / JOIN_AVAILABLE_TEAMS_FILENAME


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
        league_slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip().lower()
        if league_slug == "bowl-fantasy":
            options = ["Tokyo Katanas"]
    return [WAITLIST_OPTION, *dedupe_team_options(options)]


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
    smtp_user = str(current_app.config.get("MAIL_SMTP_USERNAME", "") or "").strip()
    smtp_pass = str(current_app.config.get("MAIL_SMTP_PASSWORD", "") or "")
    return {
        "recipient": str(current_app.config.get("JOIN_LEAGUE_RECIPIENT", "") or "").strip(),
        "host": str(current_app.config.get("MAIL_SMTP_HOST", "") or "").strip(),
        "port": int(current_app.config.get("MAIL_SMTP_PORT", 587) or 587),
        "username": smtp_user,
        "has_password": bool(smtp_pass),
        "from_addr": str(current_app.config.get("MAIL_FROM", "") or "").strip(),
        "use_tls": bool(current_app.config.get("MAIL_SMTP_USE_TLS", True)),
        "use_ssl": bool(current_app.config.get("MAIL_SMTP_USE_SSL", False)),
    }
