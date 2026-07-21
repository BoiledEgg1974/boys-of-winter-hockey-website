"""Discord outbound payloads for BOWL Six leader boards (per league mount)."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services.bowl_six import (
    AP_PRIZES,
    _real_bowl_six_week_bounds,
    gm_season_standings,
    is_current_bowl_six_week,
    season_ap_prize_for_rank,
    slate_rankings,
    slate_rankings_in_progress,
    top_players_for_slate,
)
from app.services.discord_events import (
    BOWL_SIX_EXPORT_LEADERS_EVENT_KEY,
    BOWL_SIX_LEADERS_EVENT_KEY,
    build_league_public_url,
    enqueue_discord_event,
    enqueue_repeatable_discord_event,
)
from app.services.gm_messaging import gm_discord_name
from app.site_models import BowlSixSlate, GmLeagueMembership, User

_LEADERS_PLAYER_LIMIT = 6


def _fmt_short_date(d: date, *, with_year: bool = False) -> str:
    if with_year:
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    return f"{d.strftime('%b')} {d.day}"


def _slate_week_label(slate: BowlSixSlate) -> str:
    ws = slate.week_start
    we = slate.week_end
    if ws and we and ws != we:
        return f"{_fmt_short_date(ws)} – {_fmt_short_date(we, with_year=True)}"
    if ws:
        return _fmt_short_date(ws, with_year=True)
    label = str(slate.label or "").strip()
    return label or "This week"


def _current_bowl_six_leaders_channel_id(session: Session, league_slug: str) -> str:
    from app.services.discord_events import bot_event_delivery_fields

    delivery = bot_event_delivery_fields(
        session,
        league_slug=league_slug,
        event_key=BOWL_SIX_LEADERS_EVENT_KEY,
    )
    return str(delivery.get("discord_channel_id") or "").strip()


def _message_id_for_channel(
    message_id: str | None,
    stored_channel_id: str | None,
    current_channel_id: str,
) -> str | None:
    """Only reuse a stored edit target when it belongs to the configured route channel."""
    mid = str(message_id or "").strip()
    stored = str(stored_channel_id or "").strip()
    if not mid or not current_channel_id or not stored:
        return None
    if stored != current_channel_id:
        return None
    return mid


def resolve_bowl_six_leaders_discord_message_id(
    session: Session, league_slug: str, slate: BowlSixSlate
) -> str | None:
    """Discord message id to edit for this league's live leaders post."""
    current_channel = _current_bowl_six_leaders_channel_id(session, league_slug)
    mid = _message_id_for_channel(
        getattr(slate, "discord_leaders_message_id", None),
        getattr(slate, "discord_leaders_channel_id", None),
        current_channel,
    )
    if mid:
        return mid
    prior_row = session.scalar(
        select(BowlSixSlate)
        .where(
            BowlSixSlate.league_slug == league_slug,
            BowlSixSlate.discord_leaders_message_id.is_not(None),
            BowlSixSlate.discord_leaders_message_id != "",
        )
        .order_by(BowlSixSlate.week_start.desc())
        .limit(1)
    )
    if prior_row is None:
        return None
    return _message_id_for_channel(
        getattr(prior_row, "discord_leaders_message_id", None),
        getattr(prior_row, "discord_leaders_channel_id", None),
        current_channel,
    )


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
            gm_name = gm_discord_name(user)
    return team_name, gm_name


def _clear_bowl_six_discord_leaders_targets(session: Session, league_slug: str) -> None:
    """Drop stored edit targets for every slate in a league (fresh Discord post)."""
    slug = str(league_slug or "").strip()
    if not slug:
        return
    for row in session.scalars(
        select(BowlSixSlate).where(BowlSixSlate.league_slug == slug)
    ).all():
        row.discord_leaders_message_id = None
        row.discord_leaders_channel_id = None
        row.discord_leaders_payload_hash = None


def build_bowl_six_leaders_discord_payload(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    post_new_message: bool = False,
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
                "season_ap_award": int(
                    row.get("season_ap_award") or season_ap_prize_for_rank(i)
                ),
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
        body_lines.append("Last week winners" if status == "scored" else "This week (GM)")
        for r in week_standings:
            ap_note = f" +{AP_PRIZES[r['rank']]} AP" if r["rank"] in AP_PRIZES else ""
            body_lines.append(
                f"{r['rank']}. {r['team']} ({r['gm']}) — {r['points']:.1f} pts{ap_note}"
            )

    if season_standings:
        body_lines.append("")
        body_lines.append("Season (GM)")
        for r in season_standings:
            wp = r["weeks_played"]
            suffix = f" · {wp} wk{'s' if wp != 1 else ''}" if wp else ""
            body_lines.append(
                f"{r['rank']}. {r['team']} ({r['gm']}) — {r['points']:.1f} pts{suffix} · season AP {r['season_ap_award']}"
            )

    body = "\n".join(body_lines).strip()
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
    hub_url = build_league_public_url(league_slug, "/bowl-six") or f"/{league_slug}/bowl-six"
    edit_id = None if post_new_message else resolve_bowl_six_leaders_discord_message_id(
        session, league_slug, slate
    )

    payload: dict[str, Any] = {
        "title": f"BOWL Six leaders — {week_label}",
        "body": body,
        "body_preview": body[:280],
        "url": hub_url,
        "slate_id": int(slate.id),
        "week_start": slate.week_start.isoformat() if isinstance(slate.week_start, date) else "",
        "week_label": week_label,
        "slate_status": status,
        "content_hash": content_hash,
        "top_players": top_players,
        "week_standings": week_standings,
        "season_standings": season_standings,
        "source_type": "bowl_six_slate_leaders",
        "source_id": str(int(slate.id)),
    }
    if edit_id:
        payload["edit_message_id"] = edit_id
    if post_new_message:
        payload["post_new_message"] = True
    return payload


def maybe_enqueue_bowl_six_leaders_discord(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
    *,
    force: bool = False,
    force_new_post: bool = False,
) -> bool:
    """Queue or refresh Discord leader post when content changed."""
    if str(slate.status or "") == "skipped":
        return False
    if not force_new_post and not is_current_bowl_six_week(slate):
        return False
    payload = build_bowl_six_leaders_discord_payload(
        session,
        league_session,
        slate,
        post_new_message=force_new_post,
    )
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


def enqueue_bowl_six_export_leaders_discord(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> bool:
    """Post a fresh leaders board after an export (lock-notification channel).

    One-shot post (never edits). The content hash in the source id dedupes
    repeat polls of the same export while letting every new export post again.
    """
    if str(slate.status or "") == "skipped":
        return False
    if not is_current_bowl_six_week(slate):
        return False
    payload = build_bowl_six_leaders_discord_payload(
        session, league_session, slate, post_new_message=True
    )
    payload.pop("post_new_message", None)
    payload.pop("edit_message_id", None)
    content_hash = str(payload.get("content_hash") or "")
    row = enqueue_discord_event(
        session,
        league_slug=str(slate.league_slug or ""),
        event_key=BOWL_SIX_EXPORT_LEADERS_EVENT_KEY,
        payload=payload,
        created_by_user_id=None,
        source_type="bowl_six_export_leaders",
        source_id=f"{int(slate.id)}:{content_hash}",
    )
    return row is not None


def enqueue_fresh_bowl_six_leaders_discord(
    session: Session,
    league_session: Session,
    slate: BowlSixSlate,
) -> bool:
    """Queue a new leaders Discord post (clears stored edit target first)."""
    if str(slate.status or "") == "skipped":
        return False
    league_slug = str(slate.league_slug or "")
    _clear_bowl_six_discord_leaders_targets(session, league_slug)
    session.flush()
    status = str(slate.status or "")
    if status == "open":
        from app.services.bowl_six import rs_game_ids_for_slate

        if rs_game_ids_for_slate(league_session, slate):
            from app.services.bowl_six import (
                refresh_player_week_stats,
                refresh_slate_lineup_scores,
            )

            refresh_player_week_stats(session, slate, league_session)
            refresh_slate_lineup_scores(session, league_session, slate)
    elif status in ("locked", "scored"):
        from app.services.bowl_six import (
            refresh_player_week_stats,
            refresh_slate_lineup_scores,
        )

        refresh_slate_lineup_scores(session, league_session, slate)
        refresh_player_week_stats(session, slate, league_session)
    return maybe_enqueue_bowl_six_leaders_discord(
        session, league_session, slate, force=True, force_new_post=True
    )


def record_bowl_six_leaders_discord_ack(
    session: Session,
    *,
    event_key: str,
    payload: dict,
    discord_message_id: str,
    discord_channel_id: str = "",
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
    channel_id = str(discord_channel_id or "").strip()[:32] or None
    slate.discord_leaders_message_id = mid[:32]
    slate.discord_leaders_channel_id = channel_id
    content_hash = str(payload.get("content_hash") or "").strip()
    if content_hash:
        slate.discord_leaders_payload_hash = content_hash[:64]
    week_start, _week_end = _real_bowl_six_week_bounds()
    current = session.scalar(
        select(BowlSixSlate)
        .where(
            BowlSixSlate.league_slug == slate.league_slug,
            BowlSixSlate.week_start == week_start,
        )
        .limit(1)
    )
    if current is not None and int(current.id) != int(slate.id):
        current.discord_leaders_message_id = mid[:32]
        current.discord_leaders_channel_id = channel_id
        if content_hash:
            current.discord_leaders_payload_hash = content_hash[:64]
