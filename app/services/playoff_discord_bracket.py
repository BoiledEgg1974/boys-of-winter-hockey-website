"""Discord outbound payloads for live playoff bracket series posts."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Season, Team
from app.services.discord_events import (
    PLAYOFF_BRACKET_UPDATE_EVENT_KEY,
    build_league_public_url,
    enqueue_repeatable_discord_event,
    get_league_bot_config,
    is_discord_event_route_active,
)
from app.services.playoff_bracket import (
    playoff_bracket_cache_fingerprint,
    playoff_bracket_payload,
)
from app.services.playoff_discord_predictions import (
    _series_pair_key,
    _team_meta,
    collect_bracket_series,
)
from app.services.seasons import get_current_season
from app.site_models import DiscordPlayoffBracketSeriesPost

_log = logging.getLogger(__name__)


def series_pair_key_str(series: dict[str, Any]) -> str:
    key = _series_pair_key(series)
    if key is None:
        return ""
    return f"{key[0]}-{key[1]}"


def _series_status_line(
    series: dict[str, Any], ta_meta: dict[str, Any], tb_meta: dict[str, Any]
) -> str:
    wa = int(series.get("wins_a") or 0)
    wb = int(series.get("wins_b") or 0)
    score = f"{wa}-{wb}"
    complete = bool(series.get("series_complete"))
    winner = series.get("winner") or {}
    winner_id = int(winner.get("id") or 0) if winner else 0
    ta_id = int(ta_meta.get("id") or 0)
    tb_id = int(tb_meta.get("id") or 0)
    ta_abbr = str(ta_meta.get("abbrev") or "A")
    tb_abbr = str(tb_meta.get("abbrev") or "B")

    if complete and winner_id:
        w_abbr = ta_abbr if winner_id == ta_id else tb_abbr
        return f"Series: **{score}** · **{w_abbr}** wins"
    if wa == wb:
        return f"Series: **{score}** · tied"
    if wa > wb:
        return f"Series: **{score}** · {ta_abbr} leads"
    return f"Series: **{score}** · {tb_abbr} leads"


def _series_block_text(row: dict[str, Any]) -> str:
    round_label = str(row.get("round_label") or "Series").strip()
    idx = int(row.get("series_index") or 0)
    ta = row.get("team_a") or {}
    tb = row.get("team_b") or {}
    ab_a = str(ta.get("abbrev") or ta.get("name") or "A").strip()
    ab_b = str(tb.get("abbrev") or tb.get("name") or "B").strip()
    score = str(row.get("series_score") or "").strip()
    header = f"**{round_label} · Series {idx}**"
    if score and score not in {"0-0", "0–0"}:
        header += f" ({score})"
    status = str(row.get("status_line") or "").strip()
    lines = [
        header,
        f"**{ab_a}** vs **{ab_b}**",
    ]
    if status:
        lines.append(status)
    return "\n".join(lines)


def _load_series_posts(
    session: Session, league_slug: str, season_id: int
) -> dict[str, DiscordPlayoffBracketSeriesPost]:
    rows = session.scalars(
        select(DiscordPlayoffBracketSeriesPost).where(
            DiscordPlayoffBracketSeriesPost.league_slug == league_slug,
            DiscordPlayoffBracketSeriesPost.season_id == int(season_id),
        )
    ).all()
    return {str(r.pair_key): r for r in rows}


def _clear_playoff_bracket_discord_series_posts(
    session: Session,
    league_slug: str,
    *,
    season_id: int | None = None,
) -> None:
    """Drop stored edit targets so the next delivery posts fresh series messages."""
    slug = str(league_slug or "").strip()
    if not slug:
        return
    stmt = delete(DiscordPlayoffBracketSeriesPost).where(
        DiscordPlayoffBracketSeriesPost.league_slug == slug
    )
    if season_id is not None:
        stmt = stmt.where(DiscordPlayoffBracketSeriesPost.season_id == int(season_id))
    session.execute(stmt)


def build_playoff_bracket_discord_payload(
    session: Session,
    league_session: Session,
    *,
    league_slug: str,
    post_new_messages: bool = False,
) -> dict[str, Any]:
    """Build structured payload for ``playoff_bracket_update``."""
    season = get_current_season(league_session)
    if season is None:
        return {"error": "No season is configured yet."}

    bracket = playoff_bracket_payload(int(season.id), include_team_logos=False)
    if bracket.get("projection_only"):
        return {"error": "Playoffs have not started yet."}
    if bracket.get("empty"):
        return {"error": str(bracket.get("message") or "No playoff bracket is available yet.")}

    series_rows = collect_bracket_series(bracket)
    if not series_rows:
        return {"error": "No playoff series found to post."}

    team_ids: set[int] = set()
    for _label, s in series_rows:
        for side in (s.get("team_a") or {}, s.get("team_b") or {}):
            tid = side.get("id")
            if tid:
                team_ids.add(int(tid))
    teams_by_id = (
        {
            int(t.id): t
            for t in league_session.scalars(
                select(Team).where(Team.id.in_(team_ids))
            ).all()
        }
        if team_ids
        else {}
    )

    stored = _load_series_posts(session, league_slug, int(season.id))
    formatted_series: list[dict[str, Any]] = []
    for idx, (round_label, series) in enumerate(series_rows, start=1):
        ta_json = series.get("team_a") or {}
        tb_json = series.get("team_b") or {}
        ta_id = int(ta_json.get("id") or 0)
        tb_id = int(tb_json.get("id") or 0)
        ta_meta = _team_meta(ta_json, teams_by_id.get(ta_id))
        tb_meta = _team_meta(tb_json, teams_by_id.get(tb_id))
        pair_key = series_pair_key_str(series)
        if not pair_key:
            continue

        wa = int(series.get("wins_a") or 0)
        wb = int(series.get("wins_b") or 0)
        row_data = {
            "pair_key": pair_key,
            "round_label": round_label,
            "series_index": idx,
            "team_a": ta_meta,
            "team_b": tb_meta,
            "series_score": f"{wa}-{wb}",
            "series_complete": bool(series.get("series_complete")),
            "status_line": _series_status_line(series, ta_meta, tb_meta),
        }
        block = _series_block_text(row_data)
        content_hash = hashlib.sha256(block.encode("utf-8")).hexdigest()[:32]
        stored_row = stored.get(pair_key)
        edit_id = (
            None
            if post_new_messages
            else str(getattr(stored_row, "discord_message_id", None) or "").strip() or None
        )
        formatted_series.append(
            {
                **row_data,
                "content_hash": content_hash,
                "edit_message_id": edit_id,
            }
        )

    fingerprint = playoff_bracket_cache_fingerprint(int(season.id))
    hub_url = build_league_public_url(league_slug, "/playoffs") or f"/{league_slug}/playoffs"
    note = str(bracket.get("message") or "").strip()
    payload: dict[str, Any] = {
        "title": f"Playoff bracket — {season.label}",
        "league_slug": league_slug,
        "season_id": int(season.id),
        "season_label": season.label,
        "bracket_fingerprint": fingerprint,
        "series": formatted_series,
        "series_count": len(formatted_series),
        "projection_note": note if bracket.get("projection_only") else "",
        "url": hub_url,
        "source_type": "playoff_bracket_update",
        "source_id": fingerprint,
    }
    if post_new_messages:
        payload["post_new_messages"] = True
    return {"payload": payload}


def maybe_enqueue_playoff_bracket_discord(
    session: Session,
    league_session: Session,
    league_slug: str,
    *,
    force: bool = False,
    force_new_post: bool = False,
) -> bool:
    """Queue live bracket Discord posts when playoff data changed."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    if not is_discord_event_route_active(
        session, league_slug=slug, event_key=PLAYOFF_BRACKET_UPDATE_EVENT_KEY
    ):
        return False

    result = build_playoff_bracket_discord_payload(
        session,
        league_session,
        league_slug=slug,
        post_new_messages=force_new_post,
    )
    if result.get("error"):
        return False

    disc_payload = result["payload"]
    fingerprint = str(disc_payload.get("bracket_fingerprint") or "").strip()
    bot_cfg = get_league_bot_config(session, slug)
    prev_fp = str(getattr(bot_cfg, "playoff_bracket_fingerprint", None) or "").strip()
    if not force and fingerprint and fingerprint == prev_fp:
        return False

    row = enqueue_repeatable_discord_event(
        session,
        league_slug=slug,
        event_key=PLAYOFF_BRACKET_UPDATE_EVENT_KEY,
        payload=disc_payload,
        created_by_user_id=None,
        season_id=int(disc_payload.get("season_id") or 0),
    )
    return row is not None


def enqueue_fresh_playoff_bracket_discord(
    session: Session,
    league_session: Session,
    league_slug: str,
) -> bool:
    """Queue new playoff bracket series messages (clears stored edit targets first)."""
    slug = str(league_slug or "").strip()
    if not slug:
        return False
    season = get_current_season(league_session)
    season_id = int(season.id) if season and season.id is not None else None
    _clear_playoff_bracket_discord_series_posts(session, slug, season_id=season_id)
    session.flush()
    return maybe_enqueue_playoff_bracket_discord(
        session, league_session, slug, force=True, force_new_post=True
    )


def record_playoff_bracket_discord_ack(
    session: Session,
    *,
    event_key: str,
    payload: dict,
    series_deliveries: list[dict],
) -> None:
    if str(event_key or "") != PLAYOFF_BRACKET_UPDATE_EVENT_KEY:
        return
    league_slug = str(payload.get("league_slug") or "").strip()
    if not league_slug:
        return
    try:
        season_id = int(payload.get("season_id"))
    except (TypeError, ValueError):
        return

    fingerprint = str(payload.get("bracket_fingerprint") or "").strip()
    if fingerprint:
        bot_cfg = get_league_bot_config(session, league_slug)
        bot_cfg.playoff_bracket_fingerprint = fingerprint[:128]

    series_by_key = {
        str(s.get("pair_key") or ""): s
        for s in (payload.get("series") or [])
        if isinstance(s, dict)
    }
    now = datetime.utcnow()
    for item in series_deliveries or []:
        if not isinstance(item, dict):
            continue
        pair_key = str(item.get("pair_key") or "").strip()
        mid = str(item.get("discord_message_id") or "").strip()
        if not pair_key or not mid:
            continue
        content_hash = str(series_by_key.get(pair_key, {}).get("content_hash") or "").strip()
        row = session.scalar(
            select(DiscordPlayoffBracketSeriesPost).where(
                DiscordPlayoffBracketSeriesPost.league_slug == league_slug,
                DiscordPlayoffBracketSeriesPost.season_id == season_id,
                DiscordPlayoffBracketSeriesPost.pair_key == pair_key,
            ).limit(1)
        )
        if row is None:
            row = DiscordPlayoffBracketSeriesPost(
                league_slug=league_slug,
                season_id=season_id,
                pair_key=pair_key[:32],
            )
            session.add(row)
        row.discord_message_id = mid[:32]
        if content_hash:
            row.content_hash = content_hash[:64]
        row.updated_at = now
