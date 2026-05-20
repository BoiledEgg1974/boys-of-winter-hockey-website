"""Discord outbound payloads for BOWL Six leader boards (per league mount)."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.bowl_six import (
    gm_season_standings,
    slate_rankings,
    slate_rankings_in_progress,
    top_players_for_slate,
)
from app.services.discord_events import (
    BOWL_SIX_LEADERS_EVENT_KEY,
    build_league_public_url,
    enqueue_repeatable_discord_event,
)
from app.services.gm_messaging import gm_display_name
from app.site_models import BowlSixSlate, GmLeagueMembership, User

_LEADERS_PLAYER_LIMIT = 6


def _fmt_short_date(d: date, *, with_year: bool = False) -> str:
    if with_year:
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    return f"{d.strftime('%b')} {d.day}"


def _slate_week_label(slate: BowlSixSlate) -> str:
    label = str(slate.label or "").strip()
    if label:
        return label
    ws = slate.week_start
    we = slate.week_end
    if ws and we and ws != we:
        return f"{_fmt_short_date(ws)} – {_fmt_short_date(we, with_year=True)}"
    if ws:
        return _fmt_short_date(ws, with_year=True)
    return "This week"


def _player_line_name(player: Player | None, player_id: int) -> str:
    if player is not None:
        return str(player.full_name or "").strip() or f"Player #{player_id}"
    return f"Player #{player_id}"


def _gm_row_display(
    session: Session, league_session: Session, league_slug: str, user_id: int
) -> tuple[str, str]:
    mem = session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.user_id == int(user_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    team_name = "—"
    gm_name = f"User #{user_id}"
    if mem is not None:
        team = league_session.get(Team, int(mem.team_id))
        if team is not None:
            team_name = team.full_display_name()
        user = session.get(User, int(mem.user_id))
        if user is not None:
            gm_name = gm_display_name(user)
    return team_name, gm_name


def build_bowl_six_leaders_discord_payload(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> dict[str, Any]:
    """Structured payload for ``bowl_six_leaders_update``."""
    league_slug = str(slate.league_slug or "")
    week_label = _slate_week_label(slate)
    status = str(slate.status or "open")

    top_rows = top_players_for_slate(session, slate, limit=_LEADERS_PLAYER_LIMIT)
    top_players: list[dict[str, Any]] = []
    for row in top_rows:
        player = league_session.get(Player, int(row.player_id))
        top_players.append(
            {
                "player_id": int(row.player_id),
                "name": _player_line_name(player, int(row.player_id)),
                "points": float(row.fantasy_points or 0),
            }
        )

    if status == "scored":
        gm_week = slate_rankings(session, slate)
    else:
        gm_week = slate_rankings_in_progress(session, slate)
    week_standings: list[dict[str, Any]] = []
    for i, row in enumerate(gm_week, start=1):
        team_name, gm_name = _gm_row_display(
            session, league_session, league_slug, int(row["user_id"])
        )
        week_standings.append(
            {
                "rank": i,
                "team": team_name,
                "gm": gm_name,
                "points": float(row.get("total_points") or 0),
            }
        )

    season_rows = gm_season_standings(session, league_slug)
    season_standings: list[dict[str, Any]] = []
    for i, row in enumerate(season_rows, start=1):
        team_name, gm_name = _gm_row_display(
            session, league_session, league_slug, int(row["user_id"])
        )
        season_standings.append(
            {
                "rank": i,
                "team": team_name,
                "gm": gm_name,
                "points": float(row.get("season_points") or 0),
                "weeks_played": int(row.get("weeks_played") or 0),
            }
        )

    body_lines = [f"Week: {week_label}", f"Slate status: {status}"]
    if top_players:
        body_lines.append("")
        body_lines.append("Top performers")
        for i, p in enumerate(top_players, 1):
            body_lines.append(f"{i}. {p['name']} — {p['points']:.1f} pts")
    else:
        body_lines.append("")
        body_lines.append("Top performers: no stats yet.")

    if week_standings:
        body_lines.append("")
        body_lines.append("This week (GM)")
        for r in week_standings:
            body_lines.append(
                f"{r['rank']}. {r['team']} ({r['gm']}) — {r['points']:.1f} pts"
            )

    if season_standings:
        body_lines.append("")
        body_lines.append("Season (GM)")
        for r in season_standings:
            wp = r["weeks_played"]
            suffix = f" · {wp} wk{'s' if wp != 1 else ''}" if wp else ""
            body_lines.append(
                f"{r['rank']}. {r['team']} ({r['gm']}) — {r['points']:.1f} pts{suffix}"
            )

    body = "\n".join(body_lines).strip()
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
    hub_url = build_league_public_url(league_slug, "/bowl-six") or f"/{league_slug}/bowl-six"
    edit_id = str(getattr(slate, "discord_leaders_message_id", None) or "").strip() or None

    return {
        "title": f"BOWL Six leaders — {week_label}",
        "body": body,
        "body_preview": body[:280],
        "url": hub_url,
        "slate_id": int(slate.id),
        "week_start": slate.week_start.isoformat() if isinstance(slate.week_start, date) else "",
        "week_label": week_label,
        "slate_status": status,
        "content_hash": content_hash,
        "edit_message_id": edit_id,
        "top_players": top_players,
        "week_standings": week_standings,
        "season_standings": season_standings,
        "source_type": "bowl_six_slate_leaders",
        "source_id": str(int(slate.id)),
    }


def maybe_enqueue_bowl_six_leaders_discord(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    force: bool = False,
) -> bool:
    """Queue or refresh Discord leader post when content changed."""
    if str(slate.status or "") == "skipped":
        return False
    payload = build_bowl_six_leaders_discord_payload(session, league_session, slate)
    content_hash = str(payload.get("content_hash") or "")
    prev_hash = str(getattr(slate, "discord_leaders_payload_hash", None) or "").strip()
    if not force and content_hash and content_hash == prev_hash:
        return False
    row = enqueue_repeatable_discord_event(
        session,
        league_slug=str(slate.league_slug or ""),
        event_key=BOWL_SIX_LEADERS_EVENT_KEY,
        payload=payload,
        created_by_user_id=None,
        slate_id=int(slate.id),
    )
    if row is not None and content_hash:
        slate.discord_leaders_payload_hash = content_hash
    return row is not None


def record_bowl_six_leaders_discord_ack(
    session: Session,
    *,
    event_key: str,
    payload: dict,
    discord_message_id: str,
) -> None:
    if str(event_key or "") != BOWL_SIX_LEADERS_EVENT_KEY:
        return
    mid = str(discord_message_id or "").strip()
    if not mid:
        return
    try:
        slate_id = int(payload.get("slate_id"))
    except (TypeError, ValueError):
        return
    slate = session.get(BowlSixSlate, slate_id)
    if slate is None:
        return
    slate.discord_leaders_message_id = mid[:32]
    content_hash = str(payload.get("content_hash") or "").strip()
    if content_hash:
        slate.discord_leaders_payload_hash = content_hash[:64]
